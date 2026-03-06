import smtplib
import imghdr
import os
from email.message import EmailMessage

from db import get_receiver_email, log_alert

SENDER = "dhanuvagman69@gmail.com"
PASSWORD = ""
RECEIVER = "dhanushinsit@gmail.com"

def send_emails(folder_path):
    receiver = get_receiver_email(default=RECEIVER) or RECEIVER

    msg = EmailMessage()
    msg['Subject'] = f"Malpractise Detected"
    msg['From'] = SENDER
    msg['To'] = receiver
    msg.set_content(f"Malpractise alert: Attaching all images found in '{folder_path}'")

    valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp')


    files_found = 0
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(valid_extensions):
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, 'rb') as f:
                    file_data = f.read()
                    file_type = imghdr.what(f.name) or filename.split('.')[-1]    
                msg.add_attachment(
                    file_data,
                    maintype='image',
                    subtype=file_type,
                    filename=filename)
                files_found += 1
                print(f"Attached: {filename}")
            except Exception as e:
                print(f"Error attaching {filename}: {e}")
    if files_found == 0:
        print("No valid images found in the folder.")
        try:
            log_alert(
                folder_path=folder_path,
                batch=_parse_batch(folder_path),
                receiver=receiver,
                attachments_count=0,
                status="failed",
                error_text="No valid images found",
            )
        except Exception:
            pass
        return
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER, PASSWORD)
            smtp.send_message(msg)
        print(f"\nSuccess! Sent {files_found} images.")
        try:
            log_alert(
                folder_path=folder_path,
                batch=_parse_batch(folder_path),
                receiver=receiver,
                attachments_count=files_found,
                status="sent",
            )
        except Exception:
            pass
    except Exception as e:
        print(f"Failed to send email: {e}")
        try:
            log_alert(
                folder_path=folder_path,
                batch=_parse_batch(folder_path),
                receiver=receiver,
                attachments_count=files_found,
                status="failed",
                error_text=str(e),
            )
        except Exception:
            pass


def _parse_batch(folder_path: str):
    try:
        return int(os.path.basename(folder_path).strip("/\\"))
    except Exception:
        return None