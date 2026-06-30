import asyncio
import uuid
import socket
import webbrowser
import json

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain

_pending: dict[str, tuple[asyncio.Event, dict[str, str], str]] = {}
_server_started = False
_server_port = 0

FORM_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AstrBot - 请回复</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }
  .card {
    background: #fff; border-radius: 16px; padding: 32px 28px;
    max-width: 520px; width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.15);
  }
  h2 { font-size: 18px; color: #333; margin-bottom: 8px; }
  .question {
    background: #f5f3ff; border-left: 4px solid #667eea;
    padding: 14px 16px; border-radius: 0 8px 8px 0;
    margin: 16px 0 20px; color: #444; line-height: 1.6; font-size: 15px;
    white-space: pre-wrap; word-break: break-word;
  }
  textarea {
    width: 100%; min-height: 100px; padding: 14px;
    border: 2px solid #e0e0e0; border-radius: 10px;
    font-size: 15px; font-family: inherit; resize: vertical;
    transition: border-color 0.2s;
  }
  textarea:focus { outline: none; border-color: #667eea; }
  .btn {
    margin-top: 16px; width: 100%; padding: 12px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff; border: none; border-radius: 10px;
    font-size: 16px; font-weight: 600; cursor: pointer;
    transition: opacity 0.2s;
  }
  .btn:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .done { text-align: center; padding: 40px 0; color: #2e7d32; font-size: 18px; }
  .error { text-align: center; padding: 40px 0; color: #c62828; font-size: 16px; }
</style>
</head>
<body>
<div class="card" id="app">
  <div id="form-view">
    <h2>AstrBot 向你提问</h2>
    <div class="question">__QUESTION__</div>
    <textarea id="answer" placeholder="在此输入你的回答..." autofocus></textarea>
    <button class="btn" id="submit-btn" onclick="submitAnswer()">发送回复</button>
  </div>
  <div id="done-view" style="display:none">
    <div class="done">已收到回复，可以关闭此页面。</div>
  </div>
  <div id="error-view" style="display:none">
    <div class="error" id="error-text"></div>
  </div>
</div>
<script>
const sid = '__SESSION_ID__';
function submitAnswer() {
  const text = document.getElementById('answer').value.trim();
  if (!text) return;
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = '发送中...';
  fetch('/ask/' + sid, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok) {
      document.getElementById('form-view').style.display = 'none';
      document.getElementById('done-view').style.display = 'block';
    } else {
      showError(data.error || '提交失败');
    }
  })
  .catch(e => showError('提交失败: ' + e));
}
function showError(msg) {
  document.getElementById('form-view').style.display = 'none';
  document.getElementById('error-view').style.display = 'block';
  document.getElementById('error-text').textContent = msg;
}
</script>
</body>
</html>"""


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


async def _run_http_server(port: int) -> None:
    """Minimal HTTP server using raw asyncio — zero dependency on aiohttp."""

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return

            line = request_line.decode("utf-8", errors="replace").strip()
            parts = line.split(" ")
            if len(parts) < 2:
                writer.close()
                return

            method, path = parts[0], parts[1]

            # Read headers until empty line
            headers = {}
            content_length = 0
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=5)
                hl = header_line.decode("utf-8", errors="replace").strip()
                if not hl:
                    break
                if ":" in hl:
                    k, v = hl.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            content_length = int(headers.get("content-length", 0))

            # Read body if present
            body = b""
            if content_length > 0:
                body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10)

            # --- Route: GET /ask/{sid} ---
            if method == "GET" and path.startswith("/ask/"):
                sid = path[5:]  # strip "/ask/"
                entry = _pending.get(sid)
                question = entry[2] if entry else "(会话已过期或不存在)"
                question_escaped = _html_escape(question)
                html = FORM_HTML.replace("__SESSION_ID__", sid).replace("__QUESTION__", question_escaped)
                resp = html.encode("utf-8")
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(resp)}\r\n\r\n".encode()
                    + resp
                )

            # --- Route: POST /ask/{sid} ---
            elif method == "POST" and path.startswith("/ask/"):
                sid = path[5:]
                if sid not in _pending:
                    resp = json.dumps({"ok": False, "error": "会话已过期或不存在。"}).encode()
                else:
                    try:
                        data = json.loads(body.decode("utf-8"))
                        text = str(data.get("text", "")).strip()
                    except Exception:
                        resp = json.dumps({"ok": False, "error": "无效的请求数据。"}).encode()
                    else:
                        if not text:
                            resp = json.dumps({"ok": False, "error": "回复内容不能为空。"}).encode()
                        else:
                            event, container, _ = _pending.pop(sid)
                            container["text"] = text
                            event.set()
                            resp = json.dumps({"ok": True}).encode()

                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(resp)}\r\n\r\n".encode()
                    + resp
                )
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", port)
    logger.info(f"[ask_user_tool] HTTP server started on http://127.0.0.1:{port}")
    async with server:
        await server.serve_forever()


class Main(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    async def initialize(self) -> None:
        global _server_started, _server_port

        if not _server_started:
            _server_port = _find_free_port()
            asyncio.create_task(_run_http_server(_server_port))
            _server_started = True
            await asyncio.sleep(0.3)

        context = self.context

        @filter.llm_tool(name="ask_user")
        async def ask_user(event: AstrMessageEvent, question: str) -> str:
            """Ask the user a question and block until the user responds.

            Use this tool when the AI needs to ask the user a clarifying
            question, request additional information, or get the user's
            confirmation / choice. The tool sends the question to the user
            along with a clickable link that opens a response form, then
            waits for the user to submit their answer.

            Args:
                question(string): The question text to ask the user.
            """
            session_id = uuid.uuid4().hex[:12]
            response_event = asyncio.Event()
            response_container: dict[str, str] = {}
            _pending[session_id] = (response_event, response_container, question)

            link = f"http://127.0.0.1:{_server_port}/ask/{session_id}"

            chain = MessageChain(
                chain=[
                    Comp.Plain(
                        text=f"【需要你的回复】\n\n{question}\n\n链接：{link}"
                    )
                ]
            )
            await context.send_message(event.unified_msg_origin, chain)
            webbrowser.open(link)

            try:
                await asyncio.wait_for(response_event.wait(), timeout=300)
                response_text = response_container.get("text", "(空回复)")
            except asyncio.TimeoutError:
                _pending.pop(session_id, None)
                logger.warning(f"ask_user timed out: {question[:100]}")
                response_text = "(用户未在超时时间内回复。)"
            except Exception:
                _pending.pop(session_id, None)
                logger.exception("ask_user unexpected error")
                response_text = "(等待用户回复时发生内部错误。)"

            return response_text
