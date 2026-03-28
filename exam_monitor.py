import os
import io
import time
import json
import threading
import smtplib
from email.message import EmailMessage
from email.mime.image import MIMEImage
from dotenv import load_dotenv

import cv2
import numpy as np
from PIL import Image
from google import genai
import supervision as sv

# Load secrets
load_dotenv()

# --- Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))

# Timing Config (in seconds)
ANALYSIS_INTERVAL = 5      # Check for malpractice every 5 seconds
EMAIL_COOLDOWN = 300       # Wait 5 minutes before sending another email
CONFIDENCE_THRESHOLD = 0.5 # Not used directly by Gemini JSON, but good for logic

class ExamMonitor:
    def __init__(self):
        self.client = genai.Client(api_key=GOOGLE_API_KEY)
        self.last_analysis_time = 0
        self.last_email_time = 0
        self.current_detections = sv.Detections.empty()
        self.violation_detected = False
        self.lock = threading.Lock()
        self.running = True

    def detect_malpractice(self, frame):
        """
        Runs in a background thread. Sends frame to Gemini and updates detections.
        """
        try:
            # Convert OpenCV (BGR) to PIL (RGB) for Gemini
            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(image_rgb)
            height, width = frame.shape[:2]

            prompt = """
            Analyze this image for exam malpractice. 
            Detect: 'Mobile Phone', 'Cheat Sheet', 'Smart Watch', 'Another Person'.
            Output ONLY valid JSON: [{"box_2d": [ymin, xmin, ymax, xmax], "label": "object_name"}]
            Coordinates normalized 0-1000. Return [] if safe.
            """

            response = self.client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[prompt, pil_image]
            )

            text_response = response.text.strip()
            # Clean markdown
            if "```json" in text_response:
                text_response = text_response.split("```json")[1].split("```")[0]
            elif "```" in text_response:
                text_response = text_response.replace("```", "")

            data = json.loads(text_response)
            
            # Process Detections
            if data:
                xyxy = []
                labels = []
                class_ids = []
                label_map = {"Mobile Phone": 0, "Cheat Sheet": 1, "Smart Watch": 2, "Another Person": 3}

                for item in data:
                    box = item["box_2d"]
                    label = item["label"]
                    ymin, xmin, ymax, xmax = box
                    
                    # Convert normalized 0-1000 to pixels
                    abs_xmin = (xmin / 1000) * width
                    abs_ymin = (ymin / 1000) * height
                    abs_xmax = (xmax / 1000) * width
                    abs_ymax = (ymax / 1000) * height
                    
                    xyxy.append([abs_xmin, abs_ymin, abs_xmax, abs_ymax])
                    labels.append(label)
                    class_ids.append(label_map.get(label, 99))

                detections = sv.Detections(
                    xyxy=np.array(xyxy),
                    class_id=np.array(class_ids),
                    data={"label": np.array(labels)}
                )
                
                with self.lock:
                    self.current_detections = detections
                    self.violation_detected = True
                
                # Trigger Email if cooldown passed
                self.check_and_send_email(pil_image, detections)
            else:
                with self.lock:
                    self.violation_detected = False
                    # Optional: Clear detections after some time if safe
                    # self.current_detections = sv.Detections.empty()

        except Exception as e:
            print(f"❌ Analysis Error: {e}")

    def check_and_send_email(self, image, detections):
        now = time.time()
        if now - self.last_email_time > EMAIL_COOLDOWN:
            self.send_alert_email(image, detections)
            self.last_email_time = now
        else:
            print(f"⏳ Email cooldown active. Wait {int(EMAIL_COOLDOWN - (now - self.last_email_time))}s")

    def send_alert_email(self, image, detections):
        msg = EmailMessage()
        msg['Subject'] = f"🚨 LIVE ALERT: Malpractice Detected ({len(detections)} objects)"
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER
        
        violation_list = ", ".join(detections.data['label'])
        msg.set_content(f"Violations: {violation_list}\n\nCheck attached image.")

        # Attach Annotated Image
        img_byte_arr = io.BytesIO()
        # Convert back to RGB for saving if needed, but PIL handles it
        image.save(img_byte_arr, format='JPEG')
        
        image_attachment = MIMEImage(img_byte_arr.getvalue(), name="evidence.jpg")
        msg.add_alternative(image_attachment)

        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.send_message(msg)
            print("📧 Email Sent!")
        except Exception as e:
            print(f"❌ Email Failed: {e}")

    def run(self):
        cap = cv2.VideoCapture(0) # Open Webcam
        if not cap.isOpened():
            print("Error: Could not open camera.")
            return

        print("🎥 Live Monitor Started. Press 'q' to quit.")

        while self.running:
            ret, frame = cap.read()
            if not ret:
                break

            # 1. Check if it's time to analyze
            now = time.time()
            if now - self.last_analysis_time > ANALYSIS_INTERVAL:
                self.last_analysis_time = now
                # Run detection in background thread to avoid freezing UI
                thread = threading.Thread(target=self.detect_malpractice, args=(frame,))
                thread.start()

            # 2. Draw Detections on Frame
            # Convert BGR (OpenCV) to RGB (Supervision/PIL compatible) for drawing logic
            # But Supervision annotate works on numpy arrays directly.
            # We need to ensure colors look right. Supervision expects RGB usually for internal logic 
            # but draws on numpy arrays. OpenCV is BGR.
            # To be safe with colors, we convert frame to RGB for supervision, then back to BGR for display.
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            with self.lock:
                detections = self.current_detections
                is_violation = self.violation_detected

            # Annotate
            annotated_frame = frame_rgb.copy()
            if len(detections) > 0:
                box_annotator = sv.BoxAnnotator(color=sv.Color.red())
                label_annotator = sv.LabelAnnotator(text_position=sv.Position.CENTER)
                annotated_frame = box_annotator.annotate(annotated_frame, detections)
                annotated_frame = label_annotator.annotate(annotated_frame, detections)
            
            # Convert back to BGR for OpenCV display
            display_frame = cv2.cvtColor(annotated_frame, cv2.COLOR_RGB2BGR)

            # 3. Add Status Overlay
            status = "VIOLATION DETECTED" if is_violation else "MONITORING..."
            color = (0, 0, 255) if is_violation else (0, 255, 0) # BGR (Red or Green)
            cv2.putText(display_frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
            cv2.putText(display_frame, f"Next Scan: {int(ANALYSIS_INTERVAL - (now - self.last_analysis_time))}s", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

            cv2.imshow("Exam Monitor", display_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.running = False
                break

        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    monitor = ExamMonitor()
    monitor.run()