import os
import requests

MSG91_API_KEY = os.getenv("MSG91_API_KEY")  # Put your key in .env or system env
SENDER_ID = "GVSCHL"

def send_attendance_sms(mobile, student_name, status, date):
    if not mobile:
        return
    
    message = f"Gayathri Vidyalaya:\n{student_name} is {status} today.\nDate: {date}"

    url = "https://control.msg91.com/api/v5/flow/"  # Flow API recommended
    payload = {
        "template_id": "YOUR_TEMPLATE_ID",  # Replace with MSG91 Template ID
        "sender": SENDER_ID,
        "short_url": "1",
        "mobiles": mobile,
        "var1": student_name,
        "var2": status,
        "var3": date
    }

    headers = {
        "authkey": MSG91_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        print("SMS Response:", r.json())
    except Exception as e:
        print("SMS Error:", e)
