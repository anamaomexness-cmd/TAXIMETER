from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime, date
from supabase_config import supabase, supabase_admin
import os, json, uuid
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "taxi_meter_secret_key_123")

# --- Middleware ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session or session.get('role') not in ['super_admin', 'admin']:
            flash("สิทธิ์การเข้าถึงไม่เพียงพอ", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session or session.get('role') != 'super_admin':
            flash("เฉพาะ Super Admin เท่านั้นที่สามารถเข้าถึงหน้านี้ได้", "danger")
            return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- Utils ---
def get_rent_calculation(start_date_str, end_date_str, daily_rate, promo_type):
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    delta = (end_dt - start_dt).days + 1
    
    if delta <= 0: return 0
    
    cycle = 0
    if promo_type == '2_free_1': cycle = 3
    elif promo_type == '3_free_1': cycle = 4
    elif promo_type == '5_free_1': cycle = 6
    elif promo_type == '7_free_1': cycle = 8
    
    if cycle > 0:
        free_days = delta // cycle
        paid_days = delta - free_days
    else:
        paid_days = delta
        
    return float(paid_days) * float(daily_rate)

def get_leave_days(contract_id, start_range, end_range):
    try:
        client = supabase_admin if supabase_admin else supabase
        leaves = client.table('leaves').select('*').eq('contract_id', contract_id).execute().data
        
        total_leave_days = 0
        for leave in leaves:
            leave_start = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
            leave_end = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
            
            # Intersection of [leave_start, leave_end] and [start_range, end_range]
            overlap_start = max(leave_start, start_range)
            overlap_end = min(leave_end, end_range)
            
            if overlap_start <= overlap_end:
                total_leave_days += (overlap_end - overlap_start).days + 1
                
        return total_leave_days
    except:
        return 0

# --- Routes ---

@app.route('/')
def index():
    if 'user' in session:
        if session.get('role') in ['super_admin', 'admin']:
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('driver_dashboard'))
    
    # Fetch cars for the landing page (กรองเฉพาะรถแท็กซี่)
    try:
        client = supabase_admin if supabase_admin else supabase
        res = client.table('cars').select('*').order('created_at', desc=True).execute()
        # กรองเฉพาะรถที่เป็น 'taxi' หรือยังไม่มีประเภท (ค่าเริ่มต้นเป็นแท็กซี่)
        cars = [c for c in res.data if c.get('car_type') == 'taxi' or c.get('car_type') is None]
    except Exception as e:
        print(f"Error fetching cars: {e}")
        cars = []
        
    return render_template('index.html', cars=cars)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            if res.user:
                # Fetch profile
                profile = supabase.table('profiles').select('*').eq('id', res.user.id).single().execute()
                session['user'] = res.user.id
                session['role'] = profile.data.get('role', 'driver')
                session['full_name'] = profile.data.get('full_name', email)
                
                return redirect(url_for('index'))
        except Exception as e:
            flash(f"การเข้าสู่ระบบล้มเหลว: {str(e)}", "danger")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    supabase.auth.sign_out()
    session.clear()
    return redirect(url_for('login'))

# --- Admin Routes ---

@app.route('/admin')
@admin_required
def admin_dashboard():
    # Fetch summary stats
    client = supabase_admin if supabase_admin else supabase
    cars_count = client.table('cars').select('id', count='exact').execute().count
    drivers_count = client.table('drivers').select('id', count='exact').execute().count
    active_contracts = client.table('contracts').select('id', count='exact').eq('status', 'active').execute().count
    
    # Calculate Today's Income
    today_str = date.today().isoformat()
    payments_today = client.table('payments').select('amount').gte('payment_date', f"{today_str}T00:00:00").lte('payment_date', f"{today_str}T23:59:59").execute().data
    today_income = sum(float(p['amount']) for p in payments_today)
    
    # Fetch ledger transactions to calculate cash and bank balances
    try:
        txs = client.table('ledger_transactions').select('category', 'payment_method', 'amount').execute().data
    except Exception as e:
        print(f"Error fetching ledger transactions on dashboard: {e}")
        txs = []

    cash_balance = 0
    bank_balance = 0
    for tx in txs:
        cat = tx['category']
        method = tx['payment_method']
        amt = float(tx['amount'])

        if cat == 'income':
            if method == 'cash':
                cash_balance += amt
            elif method == 'bank':
                bank_balance += amt
        elif cat == 'expense':
            if method == 'cash':
                cash_balance -= amt
            elif method == 'bank':
                bank_balance -= amt
        elif cat == 'bank_deposit':
            cash_balance -= amt
            bank_balance += amt
        elif cat == 'bank_withdrawal':
            cash_balance += amt
            bank_balance -= amt
            
    return render_template('admin/dashboard.html', 
                           cars_count=cars_count, 
                           drivers_count=drivers_count, 
                           active_contracts=active_contracts,
                           today_income=today_income,
                           cash_balance=cash_balance,
                           bank_balance=bank_balance,
                           now=datetime.now().strftime('%d %B %Y'))

@app.route('/admin/cars', methods=['GET', 'POST'])
@admin_required
def manage_cars():
    if request.method == 'POST':
        # ตัดช่องว่างออกทั้งหมดเพื่อให้ข้อมูลเป็นมาตรฐานเดียวกัน
        license_plate = "".join(request.form.get('license_plate', '').split())
        brand = request.form.get('brand')
        model = request.form.get('model')
        car_type = request.form.get('car_type', 'taxi')
        
        # Handle File Uploads
        uploaded_files = request.files.getlist('car_images')
        image_urls = []
        
        try:
            for file in uploaded_files:
                if file.filename:
                    # ใช้ Timestamp และสุ่มชื่อไฟล์เพื่อหลีกเลี่ยงอักขระภาษาไทยที่อาจมีปัญหา
                    ext = file.filename.split('.')[-1]
                    safe_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
                    file_path = f"cars/{safe_filename}"
                    
                    # อัปโหลดไปยัง Supabase Storage โดยใช้สิทธิ์ Admin
                    client = supabase_admin if supabase_admin else supabase
                    res = client.storage.from_('car_images').upload(
                        path=file_path,
                        file=file.read(),
                        file_options={"content-type": file.content_type}
                    )
                    
                    # รับ Public URL
                    public_url = client.storage.from_('car_images').get_public_url(file_path)
                    image_urls.append(public_url)
            
            # บันทึกข้อมูลลง Database โดยใช้สิทธิ์ Admin เพื่อข้าม RLS
            client = supabase_admin if supabase_admin else supabase
            client.table('cars').insert({
                "license_plate": license_plate,
                "brand": brand,
                "model": model,
                "car_type": car_type,
                "images": image_urls,
                "status": "available"
            }).execute()
            flash("บันทึกข้อมูลรถและอัปโหลดรูปภาพเรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_cars'))

    # ใช้สิทธิ์ Admin ในการดึงข้อมูลเพื่อให้เห็นครบทุกคัน
    client = supabase_admin if supabase_admin else supabase
    cars = client.table('cars').select('*').order('license_plate').execute().data
    return render_template('admin/cars.html', cars=cars)

@app.route('/admin/cars/edit/<car_id>', methods=['POST'])
@admin_required
def edit_car(car_id):
    license_plate = "".join(request.form.get('license_plate', '').split())
    brand = request.form.get('brand')
    model = request.form.get('model')
    car_type = request.form.get('car_type', 'taxi')
    status = request.form.get('status')
    
    try:
        # 1. Get kept images from form
        kept_images_json = request.form.get('kept_images', '[]')
        image_urls = json.loads(kept_images_json)
        
        # 2. Handle New File Uploads
        uploaded_files = request.files.getlist('car_images')
        client = supabase_admin if supabase_admin else supabase
        
        for file in uploaded_files:
            if file.filename:
                ext = file.filename.split('.')[-1]
                safe_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
                file_path = f"cars/{safe_filename}"
                
                client.storage.from_('car_images').upload(
                    path=file_path,
                    file=file.read(),
                    file_options={"content-type": file.content_type}
                )
                
                public_url = client.storage.from_('car_images').get_public_url(file_path)
                image_urls.append(public_url)

        # 3. Update Database
        client.table('cars').update({
            "license_plate": license_plate,
            "brand": brand,
            "model": model,
            "car_type": car_type,
            "status": status,
            "images": image_urls
        }).eq('id', car_id).execute()
        
        flash("อัปเดตข้อมูลรถและรูปภาพเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการอัปเดต: {str(e)}", "danger")
    return redirect(url_for('manage_cars'))

@app.route('/admin/cars/delete/<car_id>', methods=['POST'])
@admin_required
def delete_car(car_id):
    try:
        client = supabase_admin if supabase_admin else supabase
        client.table('cars').delete().eq('id', car_id).execute()
        flash("ลบข้อมูลรถเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการลบ: {str(e)}", "danger")
    return redirect(url_for('manage_cars'))

@app.route('/admin/drivers', methods=['GET', 'POST'])
@admin_required
def manage_drivers():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        phone = request.form.get('phone')
        license_number = request.form.get('license_number')
        id_card_number = request.form.get('id_card_number')
        address = request.form.get('address')
        deposit = float(request.form.get('deposit_balance', 0))
        target_deposit = float(request.form.get('target_deposit', 0))
        
        # Handle Photos
        photo_url = None
        license_photo_url = None
        
        try:
            # Photo Profile
            photo_file = request.files.get('photo_file')
            if photo_file and photo_file.filename:
                ext = photo_file.filename.split('.')[-1]
                path = f"drivers/profiles/{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
                client.storage.from_('driver_images').upload(path=path, file=photo_file.read(), file_options={"content-type": photo_file.content_type})
                photo_url = client.storage.from_('driver_images').get_public_url(path)

            # License Photo
            license_file = request.files.get('license_photo_file')
            if license_file and license_file.filename:
                ext = license_file.filename.split('.')[-1]
                path = f"drivers/licenses/{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
                client.storage.from_('driver_images').upload(path=path, file=license_file.read(), file_options={"content-type": license_file.content_type})
                license_photo_url = client.storage.from_('driver_images').get_public_url(path)

            client.table('drivers').insert({
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "license_number": license_number,
                "id_card_number": id_card_number,
                "address": address,
                "deposit_balance": deposit,
                "target_deposit": target_deposit,
                "photo_url": photo_url,
                "license_photo_url": license_photo_url
            }).execute()
            flash("บันทึกข้อมูลคนขับเรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_drivers'))

    drivers = client.table('drivers').select('*').order('created_at', desc=True).execute().data
    return render_template('admin/drivers.html', drivers=drivers)

@app.route('/admin/drivers/edit/<driver_id>', methods=['POST'])
@admin_required
def edit_driver(driver_id):
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    phone = request.form.get('phone')
    license_number = request.form.get('license_number')
    id_card_number = request.form.get('id_card_number')
    address = request.form.get('address')
    target_deposit = float(request.form.get('target_deposit', 0))
    deposit_balance = float(request.form.get('deposit_balance', 0))
    
    try:
        client = supabase_admin if supabase_admin else supabase
        update_data = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "license_number": license_number,
            "id_card_number": id_card_number,
            "address": address,
            "target_deposit": target_deposit,
            "deposit_balance": deposit_balance
        }
        
        # Handle Photo Updates
        photo_file = request.files.get('photo_file')
        if photo_file and photo_file.filename:
            ext = photo_file.filename.split('.')[-1]
            path = f"drivers/profiles/{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
            client.storage.from_('driver_images').upload(path=path, file=photo_file.read(), file_options={"content-type": photo_file.content_type})
            update_data["photo_url"] = client.storage.from_('driver_images').get_public_url(path)

        license_file = request.files.get('license_photo_file')
        if license_file and license_file.filename:
            ext = license_file.filename.split('.')[-1]
            path = f"drivers/licenses/{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
            client.storage.from_('driver_images').upload(path=path, file=license_file.read(), file_options={"content-type": license_file.content_type})
            update_data["license_photo_url"] = client.storage.from_('driver_images').get_public_url(path)

        client.table('drivers').update(update_data).eq('id', driver_id).execute()
        flash("อัปเดตข้อมูลคนขับเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการอัปเดต: {str(e)}", "danger")
    return redirect(url_for('manage_drivers'))

@app.route('/admin/drivers/delete/<driver_id>', methods=['POST'])
@admin_required
def delete_driver(driver_id):
    try:
        client = supabase_admin if supabase_admin else supabase
        client.table('drivers').delete().eq('id', driver_id).execute()
        flash("ลบข้อมูลคนขับเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการลบ: {str(e)}", "danger")
    return redirect(url_for('manage_drivers'))

@app.route('/admin/contracts', methods=['GET', 'POST'])
@admin_required
def manage_contracts():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        car_id = request.form.get('car_id')
        driver_id = request.form.get('driver_id')
        rental_type = request.form.get('rental_type')
        promo_type = request.form.get('promotion_type')
        rate = float(request.form.get('daily_rate', 0))
        start_date = request.form.get('start_date')
        
        try:
            # 1. Create contract
            client.table('contracts').insert({
                "car_id": car_id,
                "driver_id": driver_id,
                "rental_type": rental_type,
                "promotion_type": promo_type,
                "daily_rate": rate,
                "deposit_installment": float(request.form.get('deposit_installment', 0)),
                "start_date": start_date,
                "status": "active"
            }).execute()
            
            # 2. Update car status to 'rented'
            client.table('cars').update({"status": "rented"}).eq('id', car_id).execute()
            
            flash("เปิดสัญญาเช่าเรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_contracts'))

    contracts = client.table('contracts').select('*, cars(license_plate), drivers(first_name, last_name)').order('created_at', desc=True).execute().data
    available_cars = client.table('cars').select('*').eq('status', 'available').execute().data
    all_drivers = client.table('drivers').select('*').execute().data
    
    return render_template('admin/contracts.html', 
                           contracts=contracts, 
                           available_cars=available_cars,
                           all_drivers=all_drivers,
                           today_str=date.today().isoformat())

@app.route('/admin/leaves', methods=['POST'])
@admin_required
def manage_leaves():
    client = supabase_admin if supabase_admin else supabase
    contract_id = request.form.get('contract_id')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    reason = request.form.get('reason')
    
    try:
        client.table('leaves').insert({
            "contract_id": contract_id,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason
        }).execute()
        flash("บันทึกวันลาเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
    
    return redirect(url_for('manage_contracts'))

@app.route('/admin/payments', methods=['GET', 'POST'])
@admin_required
def manage_payments():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        contract_id = request.form.get('contract_id')
        amount = float(request.form.get('amount', 0))
        p_type = request.form.get('payment_type')
        notes = request.form.get('notes')
        receipt_no = f"REC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        try:
            res_insert = client.table('payments').insert({
                "contract_id": contract_id,
                "amount": amount,
                "payment_type": p_type,
                "notes": notes,
                "receipt_no": receipt_no
            }).execute()
            
            payment_id = res_insert.data[0]['id']
            
            # Fetch details for description and car type
            contract_details = client.table('contracts').select('*, drivers(first_name, last_name), cars(license_plate, car_type)').eq('id', contract_id).single().execute().data
            driver_name = f"{contract_details['drivers']['first_name']} {contract_details['drivers']['last_name']}"
            plate = contract_details['cars']['license_plate']
            
            type_label = 'ค่าเช่า' if p_type == 'rent' else 'ผ่อนหนี้' if p_type == 'debt' else 'เงินค้ำ' if p_type == 'deposit' else 'ค่าปรับ'
            desc = f"รายรับออโต้ ({type_label}): {driver_name} ทะเบียน {plate} บิล {receipt_no}"
            
            # Auto-sync to Ledger (only if car_type is 'taxi')
            if contract_details['cars']['car_type'] == 'taxi':
                client.table('ledger_transactions').insert({
                    "category": "income",
                    "payment_method": "cash",
                    "amount": amount,
                    "description": desc,
                    "reference_payment_id": payment_id
                }).execute()
            
            # If it's a deposit, update driver's balance
            if p_type == 'deposit':
                contract = client.table('contracts').select('driver_id').eq('id', contract_id).single().execute().data
                driver = client.table('drivers').select('deposit_balance').eq('id', contract['driver_id']).single().execute().data
                new_balance = float(driver['deposit_balance']) + amount
                client.table('drivers').update({"deposit_balance": new_balance}).eq('id', contract['driver_id']).execute()
            
            flash(f"บันทึกการชำระเงินเรียบร้อย ({'ค่าเช่า' if p_type == 'rent' else 'ผ่อนหนี้' if p_type == 'debt' else 'เงินค้ำ'}) เลขที่: {receipt_no}", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_payments'))

    payments = client.table('payments').select('*, contracts(*, drivers(first_name, last_name), cars(license_plate))').order('payment_date', desc=True).execute().data
    active_contracts = client.table('contracts').select('*, drivers(first_name, last_name), cars(license_plate)').eq('status', 'active').execute().data
    
    return render_template('admin/payments.html', payments=payments, active_contracts=active_contracts, today_str=date.today().isoformat())

@app.route('/admin/repairs', methods=['GET', 'POST'])
@admin_required
def manage_repairs():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        car_id = request.form.get('car_id')
        r_type = request.form.get('repair_type')
        desc = request.form.get('description')
        cost = float(request.form.get('cost', 0))
        r_date = request.form.get('repair_date')
        
        try:
            res_insert = client.table('repairs').insert({
                "car_id": car_id,
                "repair_type": r_type,
                "description": desc,
                "cost": cost,
                "repair_date": r_date
            }).execute()
            
            repair_id = res_insert.data[0]['id']
            
            # Fetch car details and car type
            car_details = client.table('cars').select('license_plate, car_type').eq('id', car_id).single().execute().data
            plate = car_details['license_plate']
            
            # Auto-sync to Ledger (only if car_type is 'taxi')
            if car_details['car_type'] == 'taxi':
                client.table('ledger_transactions').insert({
                    "category": "expense",
                    "payment_method": "cash",
                    "amount": cost,
                    "description": f"ค่าใช้จ่ายออโต้ (ค่าซ่อมบำรุง): ทะเบียน {plate} - {desc}",
                    "reference_repair_id": repair_id
                }).execute()
            
            flash("บันทึกรายการซ่อมเรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_repairs'))

    repairs = client.table('repairs').select('*, cars(license_plate)').order('repair_date', desc=True).execute().data
    all_cars = client.table('cars').select('*').execute().data
    return render_template('admin/repairs.html', repairs=repairs, all_cars=all_cars, today_str=date.today().isoformat())

@app.route('/admin/repairs/edit/<repair_id>', methods=['POST'])
@admin_required
def edit_repair(repair_id):
    car_id = request.form.get('car_id')
    r_type = request.form.get('repair_type')
    desc = request.form.get('description')
    cost = float(request.form.get('cost', 0))
    r_date = request.form.get('repair_date')
    
    try:
        client = supabase_admin if supabase_admin else supabase
        client.table('repairs').update({
            "car_id": car_id,
            "repair_type": r_type,
            "description": desc,
            "cost": cost,
            "repair_date": r_date
        }).eq('id', repair_id).execute()
        
        # Fetch car details and car type
        car_details = client.table('cars').select('license_plate, car_type').eq('id', car_id).single().execute().data
        plate = car_details['license_plate']
        
        # Always delete the old transaction first to prevent mismatch
        client.table('ledger_transactions').delete().eq('reference_repair_id', repair_id).execute()
        
        # Re-insert into ledger only if the car is a 'taxi'
        if car_details['car_type'] == 'taxi':
            client.table('ledger_transactions').insert({
                "category": "expense",
                "payment_method": "cash",
                "amount": cost,
                "description": f"ค่าใช้จ่ายออโต้ (ค่าซ่อมบำรุง): ทะเบียน {plate} - {desc}",
                "transaction_date": r_date,
                "reference_repair_id": repair_id
            }).execute()
        
        flash("อัปเดตรายการซ่อมเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการอัปเดต: {str(e)}", "danger")
    return redirect(url_for('manage_repairs'))

@app.route('/admin/repairs/delete/<repair_id>', methods=['POST'])
@admin_required
def delete_repair(repair_id):
    try:
        client = supabase_admin if supabase_admin else supabase
        # Delete from ledger first
        client.table('ledger_transactions').delete().eq('reference_repair_id', repair_id).execute()
        # Delete from repairs
        client.table('repairs').delete().eq('id', repair_id).execute()
        flash("ลบรายการซ่อมเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการลบ: {str(e)}", "danger")
    return redirect(url_for('manage_repairs'))

@app.route('/api/contract_debt/<contract_id>')
@admin_required
def get_contract_debt(contract_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        contract = client.table('contracts').select('*, drivers(first_name, last_name)').eq('id', contract_id).single().execute().data
        if not contract:
            return jsonify({"error": "Contract not found"}), 404
            
        start_date = datetime.strptime(contract['start_date'], '%Y-%m-%d').date()
        today = date.today()
        
        # Calculate Leave Days
        leave_days = get_leave_days(contract_id, start_date, today)
        total_days = (today - start_date).days + 1 - leave_days
        
        # Calculate Due
        promo = contract['promotion_type']
        cycle = 0
        if promo == '2_free_1': cycle = 3
        elif promo == '3_free_1': cycle = 4
        elif promo == '5_free_1': cycle = 6
        elif promo == '7_free_1': cycle = 8
        
        if cycle > 0:
            free_days = total_days // cycle
            paid_days = total_days - free_days
        else:
            paid_days = total_days
            
        total_rent_due = float(paid_days) * float(contract['daily_rate'])
        
        # Get Paid
        payments = client.table('payments').select('amount').eq('contract_id', contract_id).in_('payment_type', ['rent', 'debt']).execute().data
        total_paid = sum(float(p['amount']) for p in payments)
        
        debt = max(0, total_rent_due - total_paid)
        
        return jsonify({
            "debt": debt,
            "daily_rate": contract['daily_rate'],
            "driver_name": f"{contract['drivers']['first_name']} {contract['drivers']['last_name']}",
            "start_date": contract['start_date']
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/contract/close/<contract_id>', methods=['GET', 'POST'])
@admin_required
def close_contract(contract_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        # Fetch contract
        contract = client.table('contracts').select('*, drivers(*), cars(*)').eq('id', contract_id).single().execute().data
        if not contract:
            flash("ไม่พบข้อมูลสัญญา", "danger")
            return redirect(url_for('manage_contracts'))
            
        start_date = datetime.strptime(contract['start_date'], '%Y-%m-%d').date()
        today = date.today()
        
        # 1. Leaves
        leave_days = get_leave_days(contract_id, start_date, today)
        total_days = (today - start_date).days + 1 - leave_days
        
        # 2. Rent Due
        promo = contract['promotion_type']
        cycle = 0
        if promo == '2_free_1': cycle = 3
        elif promo == '3_free_1': cycle = 4
        elif promo == '5_free_1': cycle = 6
        elif promo == '7_free_1': cycle = 8
        
        if cycle > 0:
            free_days = max(0, total_days // cycle)
            paid_days = total_days - free_days
        else:
            paid_days = total_days
            
        total_rent_due = float(max(0, paid_days)) * float(contract['daily_rate'])
        
        # 3. Payments
        payments = client.table('payments').select('amount').eq('contract_id', contract_id).in_('payment_type', ['rent', 'debt']).execute().data
        total_paid = sum(float(p['amount']) for p in payments)
        
        current_debt = total_rent_due - total_paid
        deposit = float(contract['drivers']['deposit_balance'])
        net_balance = deposit - current_debt
        
        if request.method == 'POST':
            # PROCESS CLOSING
            # 1. Update Contract
            client.table('contracts').update({
                "status": "closed",
                "end_date": today.isoformat()
            }).eq('id', contract_id).execute()
            
            # 2. Update Car
            client.table('cars').update({"status": "available"}).eq('id', contract['car_id']).execute()
            
            flash(f"ปิดสัญญาเรียบร้อยแล้ว (รถทะเบียน {contract['cars']['license_plate']} คืนสถานะว่าง)", "success")
            return redirect(url_for('manage_contracts'))
            
        summary = {
            "total_days": (today - start_date).days + 1,
            "leave_days": leave_days,
            "net_days": total_days,
            "paid_days": paid_days,
            "total_rent_due": total_rent_due,
            "total_paid": total_paid,
            "current_debt": current_debt,
            "deposit": deposit,
            "net_balance": net_balance
        }
        
        return render_template('admin/contract_close.html', contract=contract, summary=summary, today=today)
        
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_contracts'))

@app.route('/api/leaves/<contract_id>')
@admin_required
def get_leaves_history(contract_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        leaves = client.table('leaves').select('*').eq('contract_id', contract_id).order('start_date', desc=True).execute().data
        
        # Calculate days for each leave
        for leave in leaves:
            s = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
            e = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
            leave['days_count'] = (e - s).days + 1
            
        return jsonify(leaves)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/receipt/print/<payment_id>')
@admin_required
def print_receipt(payment_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        payment = client.table('payments').select('*, contracts(*, drivers(first_name, last_name, address, phone), cars(license_plate, brand, model))').eq('id', payment_id).single().execute().data
        if not payment:
            flash("ไม่พบข้อมูลใบเสร็จ", "danger")
            return redirect(url_for('manage_payments'))
        
        return render_template('admin/receipt_print.html', payment=payment)
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_payments'))

@app.route('/admin/contract/print/<contract_id>')
@admin_required
def print_contract(contract_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        # Fetch contract
        contract = client.table('contracts').select('*, drivers(*), cars(*)').eq('id', contract_id).single().execute().data
        if not contract:
            flash("ไม่พบข้อมูลสัญญา", "danger")
            return redirect(url_for('manage_contracts'))
        
        # Fetch settings
        settings_res = client.table('settings').select('*').execute().data
        config = {item['key']: item['value'] for item in settings_res}
        
        return render_template('admin/contract_print.html', contract=contract, config=config)
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_contracts'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def manage_settings():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        try:
            # Update each setting sent in the form
            for key, value in request.form.items():
                client.table('settings').upsert({"key": key, "value": value}).execute()
            flash("บันทึกการตั้งค่าเรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_settings'))

    try:
        settings_data = client.table('settings').select('*').execute().data
        config = {item['key']: item['value'] for item in settings_data}
    except:
        config = {}
        
    return render_template('admin/settings.html', config=config)

@app.route('/admin/reports')
@admin_required
def manage_reports():
    client = supabase_admin if supabase_admin else supabase
    
    # Get filters
    report_type = request.args.get('type', 'all') # all, daily, monthly
    selected_date = request.args.get('date', date.today().isoformat())
    selected_month = request.args.get('month', date.today().strftime('%Y-%m'))
    
    try:
        # Fetch all payments and repairs (filtering later for simplicity with car types)
        # In a larger DB, we would filter in the query.
        payments_query = client.table('payments').select('*, contracts(car_id, cars(car_type, license_plate), drivers(first_name, last_name))')
        repairs_query = client.table('repairs').select('*, cars(car_type, license_plate)')
        
        # Apply Date Filters
        if report_type == 'daily':
            # payment_date is TIMESTAMPTZ, repair_date is DATE
            payments_raw = payments_query.gte('payment_date', f"{selected_date}T00:00:00").lte('payment_date', f"{selected_date}T23:59:59").execute().data
            repairs_raw = repairs_query.eq('repair_date', selected_date).execute().data
        elif report_type == 'monthly':
            # Calculate start and end of month
            year, month = map(int, selected_month.split('-'))
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            start_date = f"{selected_month}-01"
            end_date = f"{selected_month}-{last_day}"
            
            payments_raw = payments_query.gte('payment_date', f"{start_date}T00:00:00").lte('payment_date', f"{end_date}T23:59:59").execute().data
            repairs_raw = repairs_query.gte('repair_date', start_date).lte('repair_date', end_date).execute().data
        else:
            payments_raw = payments_query.execute().data
            repairs_raw = repairs_query.execute().data
        
        # Filter for taxi vehicles ONLY
        payments = [
            p for p in payments_raw 
            if p.get('contracts') and p['contracts'].get('cars') and 
               (p['contracts']['cars'].get('car_type') == 'taxi' or p['contracts']['cars'].get('car_type') is None)
        ]
        
        repairs = [
            r for r in repairs_raw 
            if r.get('cars') and (r['cars'].get('car_type') == 'taxi' or r['cars'].get('car_type') is None)
        ]
        
        # Aggregate
        summary = {
            "rent": sum(float(p['amount']) for p in payments if p['payment_type'] == 'rent'),
            "debt": sum(float(p['amount']) for p in payments if p['payment_type'] == 'debt'),
            "deposit": sum(float(p['amount']) for p in payments if p['payment_type'] == 'deposit'),
            "fine": sum(float(p['amount']) for p in payments if p['payment_type'] == 'fine'),
            "repairs": sum(float(r['cost']) for r in repairs)
        }
        
        summary["total_income"] = summary["rent"] + summary["debt"] + summary["deposit"] + summary["fine"]
        summary["net_profit"] = summary["total_income"] - summary["repairs"]
        
        return render_template('admin/reports.html', 
                               summary=summary, 
                               payments=payments, 
                               repairs=repairs,
                               report_type=report_type,
                               selected_date=selected_date,
                               selected_month=selected_month)
                               
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการดึงรายงาน: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/reports/print')
@admin_required
def print_report():
    client = supabase_admin if supabase_admin else supabase
    report_type = request.args.get('type', 'daily')
    selected_date = request.args.get('date', date.today().isoformat())
    selected_month = request.args.get('month', date.today().strftime('%Y-%m'))
    
    try:
        payments_query = client.table('payments').select('*, contracts(car_id, cars(car_type, license_plate), drivers(first_name, last_name))')
        repairs_query = client.table('repairs').select('*, cars(car_type, license_plate)')
        
        if report_type == 'daily':
            payments_raw = payments_query.gte('payment_date', f"{selected_date}T00:00:00").lte('payment_date', f"{selected_date}T23:59:59").execute().data
            repairs_raw = repairs_query.eq('repair_date', selected_date).execute().data
            title = f"รายงานประจำวันที่ {selected_date}"
        else:
            year, month = map(int, selected_month.split('-'))
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            start_date = f"{selected_month}-01"
            end_date = f"{selected_month}-{last_day}"
            payments_raw = payments_query.gte('payment_date', f"{start_date}T00:00:00").lte('payment_date', f"{end_date}T23:59:59").execute().data
            repairs_raw = repairs_query.gte('repair_date', start_date).lte('repair_date', end_date).execute().data
            title = f"รายงานประจำเดือน {selected_month}"

        payments = [p for p in payments_raw if p.get('contracts') and p['contracts'].get('cars') and (p['contracts']['cars'].get('car_type') == 'taxi' or p['contracts']['cars'].get('car_type') is None)]
        repairs = [r for r in repairs_raw if r.get('cars') and (r['cars'].get('car_type') == 'taxi' or r['cars'].get('car_type') is None)]
        
        summary = {
            "rent": sum(float(p['amount']) for p in payments if p['payment_type'] == 'rent'),
            "debt": sum(float(p['amount']) for p in payments if p['payment_type'] == 'debt'),
            "deposit": sum(float(p['amount']) for p in payments if p['payment_type'] == 'deposit'),
            "fine": sum(float(p['amount']) for p in payments if p['payment_type'] == 'fine'),
            "repairs": sum(float(r['cost']) for r in repairs)
        }
        summary["total_income"] = summary["rent"] + summary["debt"] + summary["deposit"] + summary["fine"]
        summary["net_profit"] = summary["total_income"] - summary["repairs"]

        # Fetch settings for header info
        settings_res = client.table('settings').select('*').execute().data
        config = {item['key']: item['value'] for item in settings_res}

        return render_template('admin/report_print.html', 
                               summary=summary, 
                               payments=payments, 
                               repairs=repairs, 
                               title=title,
                               config=config,
                               today=datetime.now())
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_reports'))

@app.route('/admin/staff', methods=['GET', 'POST'])
@super_admin_required
def manage_staff():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not supabase_admin:
            flash("กรุณาใส่ SUPABASE_SERVICE_ROLE_KEY ใน .env เพื่อใช้งานส่วนนี้", "danger")
            return redirect(url_for('manage_staff'))
            
        try:
            # 1. Create user in Auth
            res = supabase_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })
            
            if res.user:
                # 2. Add to profiles
                client.table('profiles').upsert({
                    "id": res.user.id,
                    "full_name": full_name,
                    "role": "admin"
                }).execute()
                flash(f"เพิ่ม Admin: {full_name} เรียบร้อยแล้ว", "success")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_staff'))

    staff_list = client.table('profiles').select('*').in_('role', ['admin', 'super_admin']).order('role').execute().data
    return render_template('admin/manage_admins.html', staff_list=staff_list)

@app.route('/admin/history')
@admin_required
def manage_history():
    client = supabase_admin if supabase_admin else supabase
    search_car = request.args.get('car', '').strip()
    search_driver = request.args.get('driver', '').strip()
    
    try:
        # Fetch contracts with relations
        query = client.table('contracts').select('*, cars(*), drivers(*)')
        
        # Note: We'll use python filtering for the names to ensure reliability with partial matches.
        contracts_raw = query.order('start_date', desc=True).execute().data
        
        contracts = []
        for c in contracts_raw:
            match_car = True
            match_driver = True
            
            if search_car:
                match_car = search_car.lower() in c['cars']['license_plate'].lower()
            
            if search_driver:
                full_name = f"{c['drivers']['first_name']} {c['drivers']['last_name']}".lower()
                match_driver = search_driver.lower() in full_name
                
            if match_car and match_driver:
                contracts.append(c)
                
        return render_template('admin/contract_history.html', 
                               contracts=contracts, 
                               search_car=search_car, 
                               search_driver=search_driver)
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/history/print')
@admin_required
def print_history():
    client = supabase_admin if supabase_admin else supabase
    search_car = request.args.get('car', '').strip()
    search_driver = request.args.get('driver', '').strip()
    
    try:
        contracts_raw = client.table('contracts').select('*, cars(*), drivers(*)').order('start_date', desc=True).execute().data
        
        contracts = []
        for c in contracts_raw:
            if search_car and search_car.lower() not in c['cars']['license_plate'].lower(): continue
            if search_driver:
                full_name = f"{c['drivers']['first_name']} {c['drivers']['last_name']}".lower()
                if search_driver.lower() not in full_name: continue
            contracts.append(c)
            
        # Fetch settings for header info
        settings_res = client.table('settings').select('*').execute().data
        config = {item['key']: item['value'] for item in settings_res}
        
        title = "รายงานประวัติการเช่ารถ"
        if search_car: title += f" - ทะเบียน {search_car}"
        if search_driver: title += f" - ชื่อ {search_driver}"

        return render_template('admin/history_print.html', 
                               contracts=contracts, 
                               title=title, 
                               config=config,
                               today=datetime.now())
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_history'))

@app.route('/admin/payment_history')
@admin_required
def manage_payment_history():
    client = supabase_admin if supabase_admin else supabase
    search_car = request.args.get('car', '').strip()
    search_driver = request.args.get('driver', '').strip()
    
    try:
        # Fetch payments with relations
        payments_raw = client.table('payments').select('*, contracts(*, cars(*), drivers(*))').order('payment_date', desc=True).execute().data
        
        payments = []
        for p in payments_raw:
            if not p.get('contracts'): continue
            
            match_car = True
            match_driver = True
            
            if search_car:
                match_car = search_car.lower() in p['contracts']['cars']['license_plate'].lower()
            
            if search_driver:
                full_name = f"{p['contracts']['drivers']['first_name']} {p['contracts']['drivers']['last_name']}".lower()
                match_driver = search_driver.lower() in full_name
                
            if match_car and match_driver:
                payments.append(p)
                
        return render_template('admin/payment_history.html', 
                               payments=payments, 
                               search_car=search_car, 
                               search_driver=search_driver)
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/payment_history/print')
@admin_required
def print_payment_history():
    client = supabase_admin if supabase_admin else supabase
    search_car = request.args.get('car', '').strip()
    search_driver = request.args.get('driver', '').strip()
    
    try:
        payments_raw = client.table('payments').select('*, contracts(*, cars(*), drivers(*))').order('payment_date', desc=True).execute().data
        
        payments = []
        for p in payments_raw:
            if not p.get('contracts'): continue
            if search_car and search_car.lower() not in p['contracts']['cars']['license_plate'].lower(): continue
            if search_driver:
                full_name = f"{p['contracts']['drivers']['first_name']} {p['contracts']['drivers']['last_name']}".lower()
                if search_driver.lower() not in full_name: continue
            payments.append(p)
            
        # Fetch settings
        settings_res = client.table('settings').select('*').execute().data
        config = {item['key']: item['value'] for item in settings_res}
        
        title = "รายงานประวัติการชำระเงิน"
        if search_car: title += f" - ทะเบียน {search_car}"
        if search_driver: title += f" - ชื่อ {search_driver}"

        return render_template('admin/payment_history_print.html', 
                               payments=payments, 
                               title=title, 
                               config=config,
                               today=datetime.now())
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_payment_history'))

# --- Ledger (Accounting) Routes ---

@app.route('/admin/ledger', methods=['GET', 'POST'])
@admin_required
def manage_ledger():
    client = supabase_admin if supabase_admin else supabase
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_manual':
            category = request.form.get('category')  # 'income' or 'expense'
            method = request.form.get('payment_method')  # 'cash' or 'bank'
            amount = float(request.form.get('amount', 0))
            desc = request.form.get('description')
            t_date = request.form.get('transaction_date', date.today().isoformat())
            try:
                client.table('ledger_transactions').insert({
                    "category": category,
                    "payment_method": method,
                    "amount": amount,
                    "description": desc,
                    "transaction_date": t_date
                }).execute()
                flash("บันทึกรายการบัญชีสำเร็จ", "success")
            except Exception as e:
                flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        elif action == 'transfer':
            transfer_type = request.form.get('transfer_type')  # 'deposit' or 'withdrawal'
            amount = float(request.form.get('amount', 0))
            t_date = request.form.get('transaction_date', date.today().isoformat())
            desc = "นำฝากเงินสดเข้าบัญชีธนาคาร" if transfer_type == 'deposit' else "ถอนเงินสดจากธนาคารมาถือเงินสด"
            try:
                client.table('ledger_transactions').insert({
                    "category": f"bank_{transfer_type}",
                    "payment_method": "cash",
                    "amount": amount,
                    "description": desc,
                    "transaction_date": t_date
                }).execute()
                flash(f"บันทึกรายการ {desc} สำเร็จ", "success")
            except Exception as e:
                flash(f"เกิดข้อผิดพลาด: {str(e)}", "danger")
        return redirect(url_for('manage_ledger'))

    try:
        txs = client.table('ledger_transactions').select('*').order('transaction_date', desc=True).order('created_at', desc=True).execute().data
    except Exception as e:
        print(f"Error fetching ledger transactions: {e}")
        txs = []

    # Calculate balances
    cash_balance = 0
    bank_balance = 0
    for tx in txs:
        cat = tx['category']
        method = tx['payment_method']
        amt = float(tx['amount'])

        if cat == 'income':
            if method == 'cash':
                cash_balance += amt
            elif method == 'bank':
                bank_balance += amt
        elif cat == 'expense':
            if method == 'cash':
                cash_balance -= amt
            elif method == 'bank':
                bank_balance -= amt
        elif cat == 'bank_deposit':
            cash_balance -= amt
            bank_balance += amt
        elif cat == 'bank_withdrawal':
            cash_balance += amt
            bank_balance -= amt

    return render_template('admin/ledger.html', 
                           transactions=txs, 
                           cash_balance=cash_balance, 
                           bank_balance=bank_balance,
                           today_str=date.today().isoformat())

@app.route('/admin/ledger/delete/<tx_id>', methods=['POST'])
@admin_required
def delete_ledger_transaction(tx_id):
    client = supabase_admin if supabase_admin else supabase
    try:
        client.table('ledger_transactions').delete().eq('id', tx_id).execute()
        flash("ลบรายการบัญชีเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาดในการลบ: {str(e)}", "danger")
    return redirect(url_for('manage_ledger'))

@app.route('/admin/ledger/print')
@admin_required
def print_ledger_report():
    client = supabase_admin if supabase_admin else supabase
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    method_filter = request.args.get('method', 'all')  # 'all', 'cash', 'bank'

    if not start_date:
        start_date = date.today().replace(day=1).isoformat()
    if not end_date:
        end_date = date.today().isoformat()

    try:
        txs = client.table('ledger_transactions').select('*').order('transaction_date', desc=False).order('created_at', desc=False).execute().data
    except Exception as e:
        print(f"Error fetching ledger for print: {e}")
        txs = []

    beginning_balance = 0
    period_transactions = []
    
    total_income = 0
    total_expense = 0
    
    for tx in txs:
        cat = tx['category']
        method = tx['payment_method']
        amt = float(tx['amount'])
        
        effect = 0
        if cat == 'income':
            if method_filter == 'all' or method == method_filter:
                effect = amt
        elif cat == 'expense':
            if method_filter == 'all' or method == method_filter:
                effect = -amt
        elif cat == 'bank_deposit':
            if method_filter == 'all':
                effect = 0
            elif method_filter == 'cash':
                effect = -amt
            elif method_filter == 'bank':
                effect = amt
        elif cat == 'bank_withdrawal':
            if method_filter == 'all':
                effect = 0
            elif method_filter == 'cash':
                effect = amt
            elif method_filter == 'bank':
                effect = -amt
                
        tx_date = tx['transaction_date'][:10]
        
        if tx_date < start_date:
            beginning_balance += effect
        elif start_date <= tx_date <= end_date:
            if method_filter == 'all' or method == method_filter or cat in ['bank_deposit', 'bank_withdrawal']:
                period_transactions.append(tx)
                
    current_running = beginning_balance
    report_txs = []
    
    for tx in period_transactions:
        cat = tx['category']
        method = tx['payment_method']
        amt = float(tx['amount'])
        
        effect = 0
        if cat == 'income':
            if method_filter == 'all' or method == method_filter:
                effect = amt
                total_income += amt
        elif cat == 'expense':
            if method_filter == 'all' or method == method_filter:
                effect = -amt
                total_expense += amt
        elif cat == 'bank_deposit':
            if method_filter == 'all':
                effect = 0
            elif method_filter == 'cash':
                effect = -amt
                total_expense += amt
            elif method_filter == 'bank':
                effect = amt
                total_income += amt
        elif cat == 'bank_withdrawal':
            if method_filter == 'all':
                effect = 0
            elif method_filter == 'cash':
                effect = amt
                total_income += amt
            elif method_filter == 'bank':
                effect = -amt
                total_expense += amt
                
        current_running += effect
        tx['running_balance'] = current_running
        report_txs.append(tx)

    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        start_thai = start_dt.strftime('%d/%m/%Y')
        end_thai = end_dt.strftime('%d/%m/%Y')
    except Exception:
        start_thai = start_date
        end_thai = end_date

    try:
        config = client.table('system_settings').select('*').single().execute().data
    except Exception:
        config = None

    return render_template('admin/ledger_report_print.html',
                           transactions=report_txs,
                           start_date=start_thai,
                           end_date=end_thai,
                           method_filter=method_filter,
                           beginning_balance=beginning_balance,
                           ending_balance=current_running,
                           total_income=total_income,
                           total_expense=total_expense,
                           config=config,
                           today=datetime.now())

# --- Driver Routes ---

@app.route('/driver')
@login_required
def driver_dashboard():
    profile_id = session.get('user')
    driver_res = supabase.table('drivers').select('*').eq('profile_id', profile_id).execute()
    driver = driver_res.data[0] if driver_res.data else None
    
    if not driver:
        flash("ไม่พบข้อมูลผู้เช่าที่เชื่อมโยงกับบัญชีนี้", "warning")
        return render_template('driver/dashboard.html', receipts=[], balance=0, current_debt=0)
    
    # Get Active Contract
    contract_res = supabase.table('contracts').select('*, cars(license_plate)').eq('driver_id', driver['id']).eq('status', 'active').execute()
    contract = contract_res.data[0] if contract_res.data else None
    
    current_debt = 0
    receipts = []
    
    if contract:
        # 1. Calculate Total Rent Due based on Promotion and Leaves
        start_date = datetime.strptime(contract['start_date'], '%Y-%m-%d').date()
        today = date.today()
        
        # Calculate Leave Days
        leave_days = get_leave_days(contract['id'], start_date, today)
        total_days = (today - start_date).days + 1 - leave_days
        
        promo = contract['promotion_type']
        cycle = 0
        if promo == '2_free_1': cycle = 3
        elif promo == '3_free_1': cycle = 4
        elif promo == '5_free_1': cycle = 6
        elif promo == '7_free_1': cycle = 8
        
        if cycle > 0:
            free_days = total_days // cycle
            paid_days = total_days - free_days
        else:
            paid_days = total_days
            
        total_rent_due = float(paid_days) * float(contract['daily_rate'])
        
        # 2. Get Total Paid
        payments_res = supabase.table('payments').select('*').eq('contract_id', contract['id']).execute()
        receipts = payments_res.data
        
        total_paid = sum(float(p['amount']) for p in receipts if p['payment_type'] in ['rent', 'debt'])
        current_debt = total_rent_due - total_paid
        
    return render_template('driver/dashboard.html', 
                           driver=driver,
                           contract=contract,
                           receipts=receipts, 
                           balance=driver.get('deposit_balance', 0),
                           current_debt=max(0, current_debt))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
