from supabase_config import supabase
import os

# --- แก้ไขข้อมูล Admin ตรงนี้ ---
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "password123"
FULL_NAME = "Super Admin"
# ----------------------------

def create_first_admin():
    try:
        # 1. สมัครสมาชิกใน Auth
        print(f"กำลังสร้างบัญชี: {ADMIN_EMAIL}...")
        res = supabase.auth.sign_up({
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        })
        
        if res.user:
            user_id = res.user.id
            print(f"สมัครสมาชิกสำเร็จ! ID: {user_id}")
            
            # 2. เพิ่มข้อมูลในตาราง profiles เป็น super_admin
            profile_data = {
                "id": user_id,
                "full_name": FULL_NAME,
                "role": "super_admin"
            }
            # ใช้ upsert เพื่อกันเหนียวหาก profile ถูกสร้างจาก trigger แล้ว
            supabase.table('profiles').upsert(profile_data).execute()
            
            print("--------------------------------------")
            print(f"สร้าง Super Admin เรียบร้อยแล้ว!")
            print(f"Email: {ADMIN_EMAIL}")
            print(f"Password: {ADMIN_PASSWORD}")
            print("คุณสามารถใช้ข้อมูลนี้ล็อกอินเข้าสู่ระบบได้เลยครับ")
            print("--------------------------------------")
        else:
            print("เกิดข้อผิดพลาดในการสร้าง User")
            
    except Exception as e:
        print(f"เกิดข้อผิดพลาด: {str(e)}")

if __name__ == "__main__":
    create_first_admin()
