
import os
import requests
from dotenv import load_dotenv

# Đọc thông tin đăng nhập từ file .env
load_dotenv()

USERNAME = os.getenv("VPBS_USERNAME")
PASSWORD = os.getenv("VPBS_PASSWORD")

# Endpoint xác thực chính thức theo tài liệu VPBankS
TOKEN_URL = "https://external.vpbanks.com.vn/auth/token"


def login():
    if not USERNAME or not PASSWORD:
        raise ValueError(
            "Chưa khai báo VPBS_USERNAME hoặc VPBS_PASSWORD trong .env"
        )

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "username": USERNAME,
        "password": PASSWORD
    }

    try:
        response = requests.post(
            TOKEN_URL,
            headers=headers,
            json=payload,
            timeout=20
        )

        print("HTTP status:", response.status_code)

        # Đọc JSON phản hồi
        try:
            result = response.json()
        except ValueError:
            print("Server không trả về JSON hợp lệ.")
            print("Response:", response.text[:500])
            return None

        # Không in toàn bộ response vì có thể chứa token
        if response.ok and result.get("status") == 1:
            data = result.get("data") or {}
            access_token = data.get("access_token")

            if not access_token:
                print("Phản hồi thành công nhưng không có access_token.")
                print("Các trường nhận được:", list(data.keys()))
                return None

            print("ĐĂNG NHẬP THÀNH CÔNG")
            print("Token type:", data.get("token_type"))
            print("Expires in:", data.get("expires_in"), "giây")
            print("Có refresh token:", bool(data.get("refresh_token")))
            print("Access token đã được nhận; không in token ra màn hình.")

            return access_token

        print("ĐĂNG NHẬP THẤT BẠI")
        print("Mã lỗi:", result.get("code"))
        print("Thông báo:", result.get("message"))
        return None

    except requests.RequestException as error:
        print("Lỗi kết nối:", error)
        return None


if __name__ == "__main__":
    token = login()

    if token:
        print("Sẵn sàng dùng token để gọi API tiếp theo.")
