from werkzeug.security import generate_password_hash
import mysql.connector

conn = mysql.connector.connect(
    host="127.0.0.1",
    user="root",
    password="",
    database="gayathrischool"
)

cur = conn.cursor()
new_pass = generate_password_hash("teacher123")
cur.execute("UPDATE teachers SET password=%s WHERE username=%s", (new_pass, "teacher"))
conn.commit()
cur.close()
conn.close()

print("✅ Teacher password reset to: teacher123")
