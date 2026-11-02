import json
import os
import websocket
from dotenv import load_dotenv
load_dotenv()
token = os.environ["NEO_ACCESS_TOKEN"]
URL = "wss://neoapi.vpbanks.com.vn/stock-stream/broker"

def on_open(ws):
    print("WebSocket đã kết nối; đang gửi xác thực...")
    ws.send(json.dumps({"action": "auth", "data": token}))

def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        print("Không phải JSON:", message)
        return

    if isinstance(data, dict) and data.get("type") == "auth":
        if data.get("status") == "success":
            print("✅ Xác thực WebSocket thành công.")
            ws.send(json.dumps({
                "type": "sub",
                "channel": "stockInfo",
                "listId": ["FPT", "HPG", "VPB"],
            }))
            print("Đã gửi yêu cầu subscribe stockInfo: FPT, HPG, VPB.")
        else:
            print("❌ VPBankS từ chối xác thực:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print("Dữ liệu nhận được:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

ws = websocket.WebSocketApp(
    "wss://neoapi.vpbanks.com.vn/stock-stream/broker",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print("Lỗi:", err),
    on_close=lambda ws, code, msg: print("Đã ngắt kết nối:", code, msg),
)
print("Đang kết nối tới VPBankS market stream...")
ws.run_forever()
