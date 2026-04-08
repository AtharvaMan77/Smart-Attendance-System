from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import math
import csv
import os
from datetime import datetime, timedelta
from deepface import DeepFace
import cv2
import numpy as np
import base64

app = Flask(__name__)
app.secret_key = "attendance_secret_key"

attendance_open = False
attendance_start_time = None
ATTENDANCE_DURATION = 15
current_subject = None 

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.csv")
ATTENDANCE_FILE = os.path.join(BASE_DIR, "attendance.csv")
FACES_DIR = os.path.join(BASE_DIR, "faces")
print("FACES_DIR =", FACES_DIR)
print("FACES folder exists:", os.path.exists(FACES_DIR))

# College Location
COLLEGE_LAT = 18.490323
COLLEGE_LON = 73.808995
RADIUS_KM = 10

# ---------------- LOGIN PAGES ----------------

@app.route('/')
def login():
    return render_template("login.html")

@app.route('/student_login')
def student_login():
    return render_template("Student_login.html")

@app.route('/faculty_login')
def faculty_login():
    return render_template("Faculty_login.html")

# ---------------- CHECK USER ----------------

def check_user(user_id, password):
    with open(USERS_FILE, 'r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header row
        for row in reader:
            if row[0].strip() == user_id and row[1].strip() == password:
                return row[2].strip()
    return None

# ---------------- LOGIN SYSTEM ----------------

@app.route('/login', methods=['POST'])
def check_login():

    user_id = request.form['student_id']
    password = request.form['password']
    ip = request.remote_addr
    role = check_user(user_id, password)
    if role == "student":
        # WiFi Check (Mobile Hotspot example)
        if not (ip.startswith("10.18.239.13") or ip == "127.0.0.1"):
            return "Connect to College WiFi / Hotspot"
        session['role'] = "student"
        session['user_id'] = user_id
        session['face_verified'] = False

        return redirect(url_for('student_dashboard'))
    elif role == "faculty":
        session['role'] = "faculty"
        return redirect(url_for('faculty_dashboard'))
    else:
        return "Invalid Login"

# ---------------- STUDENT DASHBOARD ----------------

@app.route('/student_dashboard')
def student_dashboard():
    if 'role' in session and session['role'] == "student":
        student_id = session['user_id']
        history = []
        total_classes = 0
        subject_attendance = {}

        if os.path.exists(ATTENDANCE_FILE):
            with open(ATTENDANCE_FILE, 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    if row['student_id'] == student_id:
                        history.append(row)
                        total_classes += 1

                        subject = row['subject'].strip().lower()
                        if subject not in subject_attendance:
                            subject_attendance[subject] = 0
                        subject_attendance[subject] += 1

        history.reverse()

        present_count = total_classes
        attendance_percentage = 100 if total_classes > 0 else 0

        return render_template(
            "student_dashboard.html",
            attendance_open=attendance_open,
            current_subject=current_subject,
            history=history,
            total_classes=total_classes,
            present_count=present_count,
            attendance_percentage=attendance_percentage,
            subject_attendance=subject_attendance
        )

    return redirect(url_for('student_login'))  


# ---------------- FACULTY DASHBOARD ----------------

@app.route('/faculty_dashboard')
def faculty_dashboard():

    if 'role' in session and session['role'] == "faculty":

        schedule = []
        total_classes = 0
        today_lectures = 0
        total_students = set()
        history = []

        today_day = datetime.now().strftime("%A")

        # Read Schedule
        if os.path.exists("schedule.csv"):
            with open("schedule.csv", "r", newline='', encoding='utf-8') as file:
                reader = csv.DictReader(file)

                for row in reader:
                    schedule.append(row)
                    total_classes += 1

                    if row['day'].strip() == today_day:
                        today_lectures += 1

        # Read Attendance
        if os.path.exists(ATTENDANCE_FILE):
            grouped_history = {}

            with open(ATTENDANCE_FILE, 'r', newline='', encoding='utf-8') as file:
                reader = csv.DictReader(file)

                for row in reader:
                    student_id = (row.get('student_id') or '').strip()
                    subject = (row.get('subject') or '').strip()
                    date = (row.get('date') or '').strip()
                    status = (row.get('status') or '').strip().lower()

                    total_students.add(student_id)

                    key = (date, subject)

                    if key not in grouped_history:
                        grouped_history[key] = {
                            "students": set(),
                            "present_count": 0
                        }

                    grouped_history[key]["students"].add(student_id)

                    if status == "present":
                        grouped_history[key]["present_count"] += 1

            for (date, subject), data in grouped_history.items():
                history.append({
                    "date": date,
                    "subject": subject,
                    "class_name": "N/A",
                    "total_students": len(data["students"]),
                    "present": data["present_count"]
                })

        return render_template(
            "faculty_dashboard.html",
            attendance_open=attendance_open,
            schedule=schedule,
            total_classes=total_classes,
            today_lectures=today_lectures,
            total_students=len(total_students),
            history=history
        )

    return redirect(url_for('login'))

# ---------------- OPEN ATTENDANCE ----------------

@app.route('/open_attendance', methods=['POST'])
def open_attendance():
    global attendance_open, attendance_start_time, current_subject

    subject = request.form.get('subject')

    if not subject:
        return "Subject not selected"

    attendance_open = True
    attendance_start_time = datetime.now()
    current_subject = subject

    return redirect(url_for('faculty_dashboard'))

# ---------------- CLOSE ATTENDANCE ----------------

@app.route('/close_attendance', methods=['POST'])
def close_attendance():
    global attendance_open
    attendance_open = False

    today = datetime.now().strftime("%Y-%m-%d")

    all_students = []
    present_students = []

    # Get all students
    with open(USERS_FILE, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['role'] == 'student':
                all_students.append(row['user_id'])

    # Get present students (today)
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, 'r') as f:
            reader = csv.reader(f)
            next(reader, None)

            for row in reader:
                if len(row) < 5:
                    continue
                if row[2] == today:
                    present_students.append(row[0])

    # Find absent
    absent_students = list(set(all_students) - set(present_students))

    # Save ABSENT
    for student in absent_students:
        with open(ATTENDANCE_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([student, current_subject, today, "-", "Absent"])

    return "Attendance Closed & Absentees Marked"

# ---------------- Update Schedule ----------------

@app.route('/faculty_schedule')
def faculty_schedule():
    schedule = []
    with open('schedule.csv', 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            schedule.append(row)
    return render_template("faculty_dashboard.html", schedule=schedule)

@app.route('/update_schedule', methods=['POST'])
def update_schedule():
    faculty_id = request.form['faculty_id']
    day = request.form['day']
    subject = request.form['subject']
    batch = request.form['batch']
    time = request.form['time']
    rows = []
    updated = False
    if os.path.exists("schedule.csv"):
        with open("schedule.csv", "r") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row['faculty_id'] == faculty_id and row['day'] == day:
                    row['subject'] = subject
                    row['batch'] = batch
                    row['time'] = time
                    updated = True
                rows.append(row)
    # If schedule not found → add new
    if not updated:
        rows.append({
            "faculty_id": faculty_id,
            "day": day,
            "subject": subject,
            "batch": batch,
            "time": time
        })
    with open("schedule.csv", "w", newline="") as file:
        fieldnames = ['faculty_id','day','subject','batch','time']
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return redirect(url_for('faculty_dashboard'))

# ---------------- DELETE SCHEDULE ----------------

@app.route('/delete_schedule/<int:index>', methods=['POST'])
def delete_schedule(index):
    if 'role' not in session or session['role'] != "faculty":
        return redirect(url_for('login'))

    if os.path.exists("schedule.csv"):
        with open("schedule.csv", "r") as file:
            reader = list(csv.reader(file))

        if len(reader) > 1:
            header = reader[0]
            data = reader[1:]

            if 0 <= index < len(data):
                data.pop(index)

            with open("schedule.csv", "w", newline='') as file:
                writer = csv.writer(file)
                writer.writerow(header)
                writer.writerows(data)

    return redirect(url_for('faculty_dashboard'))

# ---------------- LOCATION CHECK ----------------

def is_in_college(lat, lon):
    R = 6371
    dlat = math.radians(lat - COLLEGE_LAT)
    dlon = math.radians(lon - COLLEGE_LON)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(COLLEGE_LAT)) * math.cos(math.radians(lat)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance = R * c
    return distance <= RADIUS_KM

# ---------------- VERIFY FACE  ----------------

@app.route('/verify_face', methods=['POST'])
def verify_face():
    try:
        student_id = session.get('user_id')

        if not student_id:
            return jsonify({
                "status": "error",
                "message": "Student not logged in"
            })

        data = request.get_json()
        images = data.get("images", [])

        if not images or len(images) == 0:
            return jsonify({
                "status": "error",
                "message": "No live images received"
            })

        face_folder = FACES_DIR

        registered_images = [
            os.path.join(face_folder, f"{student_id}_1.jpg"),
            os.path.join(face_folder, f"{student_id}_2.jpg"),
            os.path.join(face_folder, f"{student_id}_3.jpg")
        ]

        print("Looking for student:", student_id)
        print("Face folder:", face_folder)

        existing_registered_images = []

        for img in registered_images:
            print("Checking:", img, "Exists:", os.path.exists(img))
            if os.path.exists(img):
                existing_registered_images.append(img)

        if len(existing_registered_images) == 0:
            return jsonify({
                "status": "error",
                "message": f"No registered face images found for {student_id}"
            })

        # Save live captured images temporarily
        temp_live_images = []
        for i, img_data in enumerate(images):
            if "," in img_data:
                img_data = img_data.split(",")[1]

            image_bytes = base64.b64decode(img_data)
            temp_path = os.path.join(face_folder, f"temp_{student_id}_{i+1}.jpg")

            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            temp_live_images.append(temp_path)

        total_match_score = 0
        total_checks = 0
        debug_results = []

        # Compare every live image with every registered image
        for live_img in temp_live_images:
            live_match = 0

            for reg_img in existing_registered_images:
                try:
                    test_img = cv2.imread(reg_img)
                    if test_img is None:
                        print("Bad registered image:", reg_img)
                        continue

                    result = DeepFace.verify(
                        img1_path=live_img,
                        img2_path=reg_img,
                        model_name="ArcFace",
                        distance_metric="cosine",
                        enforce_detection=True
                    )

                    verified = result.get("verified", False)
                    distance = result.get("distance", None)
                    threshold = result.get("threshold", None)

                    print("Live:", live_img)
                    print("Registered:", reg_img)
                    print("Verified:", verified)
                    print("Distance:", distance)
                    print("Threshold:", threshold)
                    print("-" * 40)

                    if verified:
                        live_match += 1

                except Exception as e:
                    print(f"Error comparing {live_img} with {reg_img}: {str(e)}")

            # temporary easier rule
            if live_match >= 1:
                total_match_score += 1

        # Delete temp live images
        for temp_img in temp_live_images:
            if os.path.exists(temp_img):
                os.remove(temp_img)

        # Final decision
        # Out of 3 live captures, at least 2 should be good
        if total_match_score >= 2:
            session['face_verified'] = True
            return jsonify({
                "status": "success",
                "message": "Face verified successfully",
                "match_score": total_match_score,
                "debug": debug_results
            })
        else:
            session['face_verified'] = False
            return jsonify({
                "status": "fail",
                "message": "Face not matched properly. Possible proxy or weak capture",
                "match_score": total_match_score,
                "debug": debug_results
            })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Face verification failed: {str(e)}"
        })
    
# ---------------- ATTENDANCE HISTORY ----------------
@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    global attendance_open, attendance_start_time, current_subject

    if 'user_id' not in session:
        return jsonify({"status": "fail", "message": "Student not logged in"})

    if not attendance_open:
        return jsonify({"status": "fail", "message": "Attendance session is closed"})

    if not current_subject:
        return jsonify({"status": "fail", "message": "No subject is set by faculty"})

    if not session.get('face_verified', False):
        return jsonify({"status": "fail", "message": "Face not verified"})

    student_id = session['user_id']
    subject = current_subject

    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    file_exists = os.path.exists(ATTENDANCE_FILE)

    if file_exists and os.path.getsize(ATTENDANCE_FILE) > 0:
        with open(ATTENDANCE_FILE, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if (
                    row['student_id'] == student_id and
                    row['subject'] == subject and
                    row['date'] == today
                ):
                    return jsonify({"status": "fail", "message": "Attendance already marked"})

    with open(ATTENDANCE_FILE, 'a', newline='') as file:
        fieldnames = ['student_id', 'subject', 'date', 'time', 'status']
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not file_exists or os.path.getsize(ATTENDANCE_FILE) == 0:
            writer.writeheader()

        writer.writerow({
            'student_id': student_id,
            'subject': subject,
            'date': today,
            'time': now_time,
            'status': 'Present'
        })

    session['face_verified'] = False

    return jsonify({"status": "success", "message": f"Attendance marked successfully for {subject}"})

# ---------------- MARK ATTENDANCE ----------------

@app.route('/check_location_wifi', methods=['POST'])
def check_location_wifi():
    global attendance_open

    if not attendance_open:
        return jsonify({"status": "fail", "message": "Attendance session is closed."})

    data = request.json
    lat = float(data.get('latitude'))
    lon = float(data.get('longitude'))
    subject = str(data.get('subject'))

    if not subject:
        return jsonify({"status": "fail", "message": "Subject not selected"})

    if not is_in_college(lat, lon):
        return jsonify({"status": "fail", "message": "You are not in college location!"})

    user_ip = request.remote_addr or ""
    if not (user_ip.startswith("10.181") or user_ip.startswith("192.168") or user_ip == "127.0.0.1"):
        return jsonify({"status": "fail", "message": "Connect to College WiFi / Hotspot"})

    # only allow face step, do not save attendance here
    session['subject'] = subject
    return jsonify({"status": "ok", "message": "Location and WiFi verified"})


# ---------------- ATTENDANCE HISTORY ----------------

@app.route('/attendance_history')
def attendance_history():
    history = {}
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:

                key = (row['date'], row['subject'])

                if key not in history:
                    history[key] = {
                        "date": row['date'],
                        "subject": row['subject'],
                        "class": "AD-1",
                        "total": 0,
                        "present": 0,
                        "absent": 0
                    }

                history[key]["total"] += 1

                if row.get('status') == "Present":
                    history[key]["present"] += 1
                elif row.get('status') == "Absent":
                    history[key]["absent"] += 1
    history_list = list(history.values())
    return render_template(
        "faculty_dashboard.html",
        attendance_open=attendance_open,
        history=history_list
    )

# ---------------- FACE PAGE ----------------

@app.route('/face')
def face():
    return render_template('face_attendance.html')

# ---------------- LOGOUT ----------------

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ---------------- RUN APP ----------------

if __name__ == '__main__':
   app.run(host="0.0.0.0", port=5000, debug=True)