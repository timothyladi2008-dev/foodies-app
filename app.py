import sqlite3
import time
import json
import io
import os
from flask import Flask, jsonify, request, session, render_template_string, send_file
from itsdangerous import URLSafeTimedSerializer
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

app = Flask(__name__)
# NOTE: for production, set this from an environment variable instead of hardcoding it.
app.secret_key = os.environ.get("FOODIES_SECRET_KEY", "foodies_permanent_secure_session_key_2026")
DB_FILE = "users.db"

serializer = URLSafeTimedSerializer(app.secret_key)
active_visitors = {}

rider_status = {
    "lat": 9.05785,
    "lng": 7.49508,
    "dest_lat": 9.07647,
    "dest_lng": 7.39857,
    "status": "In Transit"
}


# ---------------------------------------------------------
# DATABASE SETUP & HELPERS
# ---------------------------------------------------------
def _ensure_orders_schema(conn):
    """
    If a users.db already exists from an older/different version of this app,
    'CREATE TABLE IF NOT EXISTS orders' silently does nothing even when that
    existing table is missing columns the app now needs (e.g. order_id).
    Detect that and migrate the old table out of the way instead of crashing
    on every insert.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    if not cursor.fetchone():
        return  # table doesn't exist yet, nothing to migrate

    cursor.execute("PRAGMA table_info(orders)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    required_columns = {"order_id", "email", "items", "total", "status", "address", "created_at", "restaurant"}

    if required_columns.issubset(existing_columns):
        return  # schema is already correct

    backup_name = f"orders_legacy_{int(time.time())}"
    cursor.execute(f"ALTER TABLE orders RENAME TO {backup_name}")
    conn.commit()
    print(f"[foodies] Old 'orders' table was missing required columns; "
          f"renamed it to '{backup_name}' and created a fresh 'orders' table.")


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS users
                       (
                           email TEXT PRIMARY KEY,
                           password TEXT NOT NULL,
                           pin TEXT NOT NULL,
                           is_verified INTEGER DEFAULT 0,
                           phone TEXT DEFAULT '',
                           address TEXT DEFAULT ''
                       )
                       ''')
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS chat_messages
                       (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           email TEXT,
                           sender TEXT,
                           message TEXT,
                           timestamp REAL
                       )
                       ''')
        conn.commit()

        _ensure_orders_schema(conn)

        # 'items' stores a JSON array of {name, price} so a receipt can be
        # rebuilt purely from the database, independent of the browser session.
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS orders
                       (
                           order_id TEXT PRIMARY KEY,
                           email TEXT,
                           items TEXT,
                           total REAL,
                           status TEXT,
                           address TEXT,
                           created_at REAL,
                           restaurant TEXT DEFAULT ''
                       )
                       ''')
        conn.commit()


init_db()


def get_user(email):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, pin, is_verified, phone, address FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return {
                "email": row[0],
                "password": row[1],
                "pin": row[2],
                "is_verified": row[3],
                "phone": row[4],
                "address": row[5]
            }
    return None


def save_user(email, password, pin, is_verified=0):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (email, password, pin, is_verified) VALUES (?, ?, ?, ?)",
                       (email, password, pin, is_verified))
        conn.commit()


def get_order(order_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT order_id, email, items, total, status, address, created_at, restaurant FROM orders WHERE order_id = ?",
            (order_id,))
        row = cursor.fetchone()
        if not row:
            return None
        try:
            items = json.loads(row[2])
        except (TypeError, ValueError):
            # Backward compatibility with older rows that stored a plain comma-joined string.
            items = [{"name": n.strip(), "price": 0} for n in (row[2] or "").split(",") if n.strip()]
        return {
            "order_id": row[0],
            "email": row[1],
            "items": items,
            "total": row[3],
            "status": row[4],
            "address": row[5],
            "created_at": row[6],
            "restaurant": row[7] if len(row) > 7 else ''
        }


def extract_username(email):
    return email.split('@')[0] if email and '@' in email else email


@app.before_request
def track_visitors():
    active_visitors[request.remote_addr] = time.time()


# ---------------------------------------------------------
# AUTHENTICATION & PROFILE ENDPOINTS
# ---------------------------------------------------------
@app.route('/api/signup', methods=['POST'])
def api_signup():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    pin = data.get('pin')

    if not email or not password or not pin:
        return jsonify({"success": False, "error": "All fields are required"}), 400

    if get_user(email):
        return jsonify({"success": False, "error": "Email already registered"}), 400

    save_user(email, password, pin, is_verified=1)
    session['user_email'] = email
    return jsonify({"success": True, "email": email, "username": extract_username(email)})


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    user = get_user(email)
    if user and user['password'] == password:
        session['temp_user_email'] = email
        return jsonify({"require_pin": True})

    return jsonify({"success": False, "error": "Invalid email or password"})


@app.route('/api/verify-pin', methods=['POST'])
def api_verify_pin():
    data = request.get_json()
    pin = data.get('pin')
    email = session.get('temp_user_email')

    if not email:
        return jsonify({"success": False, "error": "Session expired. Please log in again."}), 400

    user = get_user(email)
    if user and user['pin'] == pin:
        session['user_email'] = email
        session.pop('temp_user_email', None)
        return jsonify({"success": True, "email": email, "username": extract_username(email)})

    return jsonify({"success": False, "error": "Incorrect security PIN"})


@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    data = request.get_json()
    email = data.get('email')
    user = get_user(email)
    if not user:
        return jsonify({"success": False, "error": "Email address not found"})

    token = serializer.dumps(email, salt='password-reset-salt')
    reset_link = f"/reset-password/{token}"
    return jsonify({"success": True, "message": "Password reset link generated!", "debug_link": reset_link})


@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    data = request.get_json()
    token = data.get('token')
    new_password = data.get('password')
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=900)
    except Exception:
        return jsonify({"success": False, "error": "Reset link is invalid or has expired."}), 400

    user = get_user(email)
    if user:
        save_user(email, new_password, user['pin'], user['is_verified'])
        return jsonify({"success": True, "message": "Password updated successfully!"})
    return jsonify({"success": False, "error": "User not found."}), 400


@app.route('/api/get-profile', methods=['GET'])
def api_get_profile():
    email = session.get('user_email')
    if not email:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    user = get_user(email)
    if user:
        return jsonify({"success": True, "phone": user['phone'], "address": user['address']})
    return jsonify({"success": False, "error": "User not found"}), 404


@app.route('/api/update-profile', methods=['POST'])
def api_update_profile():
    email = session.get('user_email')
    if not email:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json()
    phone = data.get('phone', '')
    address = data.get('address', '')

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET phone = ?, address = ? WHERE email = ?", (phone, address, email))
        conn.commit()
    return jsonify({"success": True, "message": "Profile updated successfully"})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({"success": True})


# ---------------------------------------------------------
# ORDER, PDF RECEIPT & CHAT ENDPOINTS
# ---------------------------------------------------------
@app.route('/api/checkout', methods=['POST'])
def api_checkout():
    email = session.get('user_email')
    if not email:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    items = data.get('items', [])
    address = (data.get('address') or '').strip()
    restaurant = (data.get('restaurant') or '').strip()

    if not items:
        return jsonify({"success": False, "error": "Your cart is empty"}), 400
    if not address:
        return jsonify({"success": False, "error": "Delivery address is required"}), 400

    # Keep only the fields we need, so we never persist unexpected client data.
    clean_items = [{"name": i.get('name', 'Item'), "price": float(i.get('price', 0) or 0)} for i in items]
    total = sum(i['price'] for i in clean_items)
    order_id = f"ORD-{int(time.time() * 1000)}"

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO orders (order_id, email, items, total, status, address, created_at, restaurant) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, email, json.dumps(clean_items), total, 'Out for Delivery', address, time.time(), restaurant))
            conn.commit()
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not save order: {e}"}), 500

    # Session only needs to remember *which* order was last placed.
    session['last_order_id'] = order_id
    return jsonify({"success": True, "order_id": order_id, "total": total})


@app.route('/api/download-receipt', methods=['GET'])
def download_receipt():
    email = session.get('user_email')
    if not email:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    order_id = request.args.get('order_id') or session.get('last_order_id')
    if not order_id:
        return jsonify({"success": False, "error": "No order found to generate a receipt for"}), 404

    order = get_order(order_id)
    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404
    if order['email'] != email:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        story = []

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=22,
                                      textColor=colors.HexColor('#ff4757'), alignment=1)
        sub_style = ParagraphStyle('SubStyle', parent=styles['Normal'], fontSize=11,
                                    textColor=colors.HexColor('#64748b'), alignment=1)
        body_style = ParagraphStyle('BodyStyle', parent=styles['Normal'], fontSize=10,
                                     textColor=colors.HexColor('#1e293b'))

        story.append(Paragraph("Foodies.", title_style))
        story.append(Paragraph("Official Payment & Delivery Invoice", sub_style))
        story.append(Spacer(1, 15))

        created = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(order['created_at']))
        restaurant_line = f"<b>Restaurant:</b> {order['restaurant']}<br/>" if order.get('restaurant') else ""
        meta_text = (f"<b>Order ID:</b> {order['order_id']}<br/>"
                     f"{restaurant_line}"
                     f"<b>Customer:</b> {order['email']}<br/>"
                     f"<b>Delivery Address:</b> {order['address']}<br/>"
                     f"<b>Date:</b> {created}")
        story.append(Paragraph(meta_text, body_style))
        story.append(Spacer(1, 15))

        table_data = [["Item Description", "Price (\u20a6)"]]
        for item in order['items']:
            table_data.append([item.get('name', 'Meal'), f"\u20a6{item.get('price', 0):,.0f}"])
        table_data.append(["Total Amount Paid", f"\u20a6{order['total']:,.0f}"])

        t = Table(table_data, colWidths=[350, 150])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff4757')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f1f5f9')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        story.append(t)
        story.append(Spacer(1, 25))
        story.append(
            Paragraph("Thank you for ordering with Foodies! For support, contact rider or support@foodies.com.",
                      sub_style))

        doc.build(story)
        buffer.seek(0)
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not generate receipt: {e}"}), 500

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{order['order_id']}_receipt.pdf"
    )


@app.route('/api/rider-location', methods=['GET'])
def get_rider_location():
    return jsonify(rider_status)


@app.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    email = session.get('user_email')
    if not email:
        return jsonify([])
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE email = ? ORDER BY id ASC", (email,))
        rows = cursor.fetchall()
        history = [{"sender": r[0], "message": r[1], "timestamp": r[2]} for r in rows]
    return jsonify(history)


@app.route('/api/rider-chat', methods=['POST'])
def rider_chat():
    email = session.get('user_email')
    data = request.get_json()
    user_msg = data.get('message', '').lower()

    if "hello" in user_msg or "hi" in user_msg:
        reply = "Hello there! I am on my way with your delicious order."
    elif "where" in user_msg:
        reply = "I'm navigating through traffic right now. Check the live map above!"
    elif "time" in user_msg or "long" in user_msg:
        reply = "I should arrive at your location in about 10-15 minutes."
    else:
        reply = "Got it! I'll make sure to deliver your food hot and fresh."

    if email:
        current_time = time.time()
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO chat_messages (email, sender, message, timestamp) VALUES (?, ?, ?, ?)",
                           (email, 'user', data.get('message'), current_time))
            cursor.execute("INSERT INTO chat_messages (email, sender, message, timestamp) VALUES (?, ?, ?, ?)",
                           (email, 'rider', reply, current_time + 0.1))
            conn.commit()

    return jsonify({"reply": reply})


# ---------------------------------------------------------
# ADMIN DASHBOARD ENDPOINTS
# ---------------------------------------------------------
@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    email = session.get('user_email')
    if not email or "admin" not in email:
        return jsonify({"success": False, "error": "Unauthorized access"}), 403

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(total), COUNT(*) FROM orders")
        row = cursor.fetchone()
        total_revenue = row[0] or 0
        total_orders = row[1] or 0
        cursor.execute("SELECT email, phone, address FROM users")
        users_count = len(cursor.fetchall())

    return jsonify({
        "success": True,
        "revenue": total_revenue,
        "orders_count": total_orders,
        "users_count": users_count,
        "active_visitors": len(active_visitors)
    })


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


# ---------------------------------------------------------
# FRONTEND TEMPLATE
# ---------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Foodies | Premium Culinary Delivery</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://js.paystack.co/v1/inline.js"></script>

  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <style>
    :root {
      --primary: #ff4757;
      --primary-hover: #ff6b81;
      --accent: #2ed573;
      --dark: #0f172a;
      --bg-gradient: linear-gradient(180deg, #38bdf8 0%, #7dd3fc 35%, #bae6fd 70%, #e0f2fe 100%);
      --card-bg: rgba(255, 255, 255, 0.85);
      --text-main: #1e293b;
      --text-muted: #64748b;
      --border: rgba(255, 255, 255, 0.6);
      --glass-shadow: 0 20px 40px rgba(0, 0, 0, 0.08);
      --input-bg: #ffffff;
    }

    [data-theme="dark"] {
      --primary: #ff4757;
      --primary-hover: #ff6b81;
      --accent: #2ed573;
      --dark: #f8fafc;
      --bg-gradient: linear-gradient(180deg, #020617 0%, #0f172a 50%, #1e293b 100%);
      --card-bg: rgba(15, 23, 42, 0.85);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --border: rgba(255, 255, 255, 0.1);
      --glass-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
      --input-bg: #1e293b;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; transition: background 0.3s, color 0.3s; }

    body { 
      min-height: 100vh; background: var(--bg-gradient); background-attachment: fixed; 
      color: var(--text-main); position: relative; overflow-x: hidden;
    }

    .bg-watermarks {
      position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; pointer-events: none; z-index: 0; overflow: hidden;
    }

    .watermark {
      position: absolute; font-size: clamp(3rem, 6vw, 7rem); font-weight: 900;
      color: rgba(255, 255, 255, 0.12); text-transform: uppercase; letter-spacing: -2px; user-select: none;
    }

    .wm-1 { top: 5%; left: -2%; transform: rotate(-12deg); }
    .wm-2 { top: 18%; right: 5%; transform: rotate(15deg); }
    .wm-3 { top: 45%; left: 10%; transform: rotate(-8deg); }
    .wm-4 { top: 60%; right: -3%; transform: rotate(10deg); }
    .wm-5 { bottom: 5%; left: 35%; transform: rotate(-5deg); }

    .hidden { display: none !important; }

    .auth-container { 
      min-height: 100vh; display: flex; justify-content: center; align-items: center; 
      padding: 20px; position: relative; z-index: 1;
    }

    .auth-card { 
      background: var(--card-bg); backdrop-filter: blur(20px); border-radius: 24px; 
      border: 1px solid var(--border); box-shadow: var(--glass-shadow); padding: 40px 32px; width: 100%; max-width: 380px; text-align: center; 
    }

    .brand-logo { font-size: 26px; font-weight: 800; color: var(--text-main); letter-spacing: -0.5px; }
    .brand-logo span { color: var(--primary); }

    .auth-subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; margin-bottom: 24px; }

    .form-group { margin-bottom: 14px; text-align: left; }
    .form-group label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; display: block; }

    input, textarea { 
      width: 100%; padding: 12px 16px; border-radius: 12px; border: 1px solid #cbd5e1; 
      background: var(--input-bg); color: var(--text-main); outline: none; font-size: 13px; transition: all 0.2s ease; 
    }

    input:focus, textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(255, 71, 87, 0.15); }

    .btn-primary { 
      width: 100%; padding: 12px; font-weight: 700; font-size: 13px; 
      background: linear-gradient(135deg, var(--primary), #ff6348); 
      color: white; border: none; border-radius: 12px; cursor: pointer; 
      transition: all 0.2s; box-shadow: 0 4px 12px rgba(255, 71, 87, 0.25);
    }

    .btn-primary:hover { opacity: 0.95; transform: translateY(-1px); }
    .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

    .receipt-box { text-align: left; }
    .receipt-check {
      width: 52px; height: 52px; border-radius: 50%; background: linear-gradient(135deg, #10b981, #059669);
      color: white; font-size: 26px; font-weight: 800; display: flex; align-items: center; justify-content: center;
      margin: 0 auto 14px; box-shadow: 0 8px 20px rgba(16, 185, 129, 0.35);
    }

    .cs-section { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border); }
    .cs-section h4 { font-size: 13px; font-weight: 800; color: var(--text-main); margin-bottom: 4px; }
    .cs-section p { font-size: 11px; color: var(--text-muted); margin-bottom: 10px; }
    .cs-link {
      display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--text-main);
      background: rgba(0,0,0,0.03); border: 1px solid var(--border); border-radius: 12px;
      padding: 10px 12px; margin-bottom: 8px; transition: all 0.2s;
    }
    .cs-link:hover { background: rgba(255, 71, 87, 0.08); border-color: var(--primary); }
    .cs-icon { font-size: 18px; }
    .cs-link .cs-label { display: block; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
    .cs-link .cs-value { display: block; font-size: 13px; font-weight: 700; }

    .toggle-link { margin-top: 16px; font-size: 12px; color: var(--text-main); cursor: pointer; font-weight: 600; text-decoration: underline; }

    .app-container { max-width: 1280px; margin: 0 auto; padding: 24px; position: relative; z-index: 1; }

    nav { 
      display: flex; justify-content: space-between; align-items: center; 
      padding: 16px 28px; background: var(--card-bg); backdrop-filter: blur(16px); 
      border-radius: 20px; border: 1px solid var(--border); box-shadow: var(--glass-shadow); margin-bottom: 30px; 
    }

    .nav-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

    .user-badge { 
      font-size: 13px; font-weight: 600; background: rgba(15, 23, 42, 0.05); 
      padding: 8px 14px; border-radius: 10px; color: var(--text-main); 
    }

    .btn-nav-action { 
      padding: 8px 14px; border-radius: 10px; font-size: 12px; font-weight: 700; 
      cursor: pointer; border: none; display: flex; align-items: center; gap: 6px; transition: all 0.2s; 
    }

    .btn-cart { background: var(--primary); color: white; box-shadow: 0 4px 12px rgba(255, 71, 87, 0.2); }
    .btn-cart-count { background: white; color: var(--primary); padding: 2px 7px; border-radius: 20px; font-size: 11px; }
    .btn-rider { background: #10b981; color: white; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.2); }
    .btn-profile { background: #3b82f6; color: white; }
    .btn-admin { background: #8b5cf6; color: white; }
    .btn-logout { background: #e2e8f0; color: #475569; }
    .btn-theme { background: transparent; border: 1px solid var(--border); font-size: 16px; cursor: pointer; padding: 6px 10px; border-radius: 10px; }

    .hero-section { text-align: center; margin: 20px 0 32px; }
    .hero-section h1 { font-size: 32px; font-weight: 800; color: var(--text-main); letter-spacing: -1px; }
    .hero-section p { font-size: 14px; color: var(--text-muted); margin-top: 4px; font-weight: 500; }

    .search-wrapper { max-width: 550px; margin: 20px auto 0; position: relative; }
    .search-wrapper input { padding-left: 20px; height: 50px; border-radius: 30px; border: 1px solid var(--border); background: var(--card-bg); box-shadow: var(--glass-shadow); font-size: 14px; }

    .categories-row { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 10px; margin-bottom: 30px; }
    .category-pill { 
      background: var(--card-bg); padding: 10px 20px; border-radius: 40px; 
      border: 1px solid var(--border); cursor: pointer; font-size: 13px; font-weight: 600; 
      color: var(--text-muted); white-space: nowrap; transition: all 0.2s; 
    }

    .category-pill.active { background: var(--text-main); color: var(--card-bg); border-color: var(--text-main); box-shadow: 0 4px 12px rgba(15, 23, 42, 0.15); }

    .food-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }

    .restaurant-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 20px; }

    .restaurant-card {
      background: var(--card-bg); backdrop-filter: blur(12px); border-radius: 20px;
      border: 1px solid var(--border); padding: 18px; cursor: pointer;
      transition: transform 0.2s, box-shadow 0.2s; box-shadow: var(--glass-shadow);
      display: flex; flex-direction: column; gap: 10px;
    }
    .restaurant-card:hover { transform: translateY(-4px); }
    .restaurant-card-top { display: flex; align-items: center; gap: 12px; }
    .restaurant-logo {
      width: 52px; height: 52px; border-radius: 14px; background: rgba(0,0,0,0.04);
      display: flex; align-items: center; justify-content: center; font-size: 26px; flex-shrink: 0;
    }
    .restaurant-name { font-size: 15px; font-weight: 800; color: var(--text-main); }
    .restaurant-cuisine { font-size: 11px; color: var(--text-muted); font-weight: 600; margin-top: 2px; }
    .restaurant-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); font-weight: 600; }
    .restaurant-meta .rating { color: #f59e0b; }
    .restaurant-view-btn {
      background: var(--text-main); color: var(--card-bg); border: none; padding: 8px 14px;
      border-radius: 10px; font-size: 11px; font-weight: 700; cursor: pointer; text-align: center;
    }

    .restaurant-header {
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
      background: var(--card-bg); backdrop-filter: blur(12px); border: 1px solid var(--border);
      border-radius: 18px; padding: 14px 18px; margin-bottom: 20px; box-shadow: var(--glass-shadow);
    }
    .btn-back {
      background: rgba(0,0,0,0.05); border: none; color: var(--text-main); font-size: 12px; font-weight: 700;
      padding: 8px 14px; border-radius: 10px; cursor: pointer;
    }
    .restaurant-header-info { display: flex; align-items: center; gap: 10px; }
    .rh-logo { font-size: 24px; }
    .rh-name { font-size: 15px; font-weight: 800; color: var(--text-main); }
    .rh-meta { font-size: 11px; color: var(--text-muted); font-weight: 600; }

    .food-card { 
      background: var(--card-bg); backdrop-filter: blur(12px); border-radius: 20px; 
      border: 1px solid var(--border); padding: 16px; display: flex; flex-direction: column; 
      justify-content: space-between; transition: transform 0.2s, box-shadow 0.2s; box-shadow: var(--glass-shadow); 
    }

    .food-card:hover { transform: translateY(-4px); }

    .food-img-frame { 
      width: 100%; height: 120px; border-radius: 14px; overflow: hidden; margin-bottom: 12px; 
      background: rgba(0,0,0,0.03); display: flex; align-items: center; justify-content: center; font-size: 52px;
    }

    .food-title { font-size: 15px; font-weight: 700; color: var(--text-main); }
    .food-desc { font-size: 11px; color: var(--text-muted); margin-top: 4px; height: 32px; overflow: hidden; line-height: 1.4; }

    .food-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; }
    .food-price { font-size: 15px; font-weight: 800; color: var(--primary); }
    .btn-add { background: var(--text-main); color: var(--card-bg); border: none; padding: 8px 14px; border-radius: 10px; font-size: 11px; font-weight: 700; cursor: pointer; }

    .skeleton-card {
      background: var(--card-bg); border-radius: 20px; padding: 16px; height: 260px;
      animation: pulse 1.5s infinite ease-in-out; border: 1px solid var(--border);
    }
    .skeleton-line { background: rgba(150,150,150,0.2); border-radius: 8px; margin-bottom: 10px; }
    @keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }

    .modal-overlay { 
      position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
      background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(8px); 
      display: flex; justify-content: center; align-items: center; z-index: 100; padding: 20px; 
    }

    .modal-box { background: var(--card-bg); backdrop-filter: blur(20px); border-radius: 24px; padding: 28px; width: 100%; max-width: 440px; box-shadow: 0 25px 50px rgba(0,0,0,0.3); border: 1px solid var(--border); color: var(--text-main); }

    .milestone-container { display: flex; justify-content: space-between; margin: 20px 0; position: relative; }
    .milestone-step { display: flex; flex-direction: column; align-items: center; font-size: 10px; font-weight: 700; color: var(--text-muted); z-index: 2; width: 25%; text-align: center; }
    .milestone-circle { width: 24px; height: 24px; border-radius: 50%; background: #cbd5e1; display: flex; align-items: center; justify-content: center; color: white; margin-bottom: 6px; font-size: 11px; }
    .milestone-step.active .milestone-circle { background: #10b981; box-shadow: 0 0 10px rgba(16,185,129,0.5); }
    .milestone-step.active { color: #10b981; }

    .chat-box { 
      background: var(--card-bg); border-radius: 24px; width: 100%; max-width: 460px; height: 580px; 
      display: flex; flex-direction: column; box-shadow: 0 25px 50px rgba(0,0,0,0.3); overflow: hidden; border: 1px solid var(--border);
    }
    .chat-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
    #rider-map { height: 180px; width: 100%; border-bottom: 1px solid var(--border); }
    .chat-body { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 10px; }
    .chat-msg { max-width: 80%; padding: 10px 14px; border-radius: 16px; font-size: 12px; line-height: 1.5; }
    .chat-msg.rider { background: rgba(255,255,255,0.1); border: 1px solid var(--border); color: var(--text-main); align-self: flex-start; }
    .chat-msg.user { background: var(--primary); color: white; align-self: flex-end; }
    .chat-input-area { padding: 14px 18px; border-top: 1px solid var(--border); display: flex; gap: 8px; }

    #toast-container { position: fixed; bottom: 20px; right: 20px; display: flex; flex-direction: column; gap: 10px; z-index: 300; }
    .toast-item { background: var(--text-main); color: var(--card-bg); padding: 12px 20px; border-radius: 12px; font-size: 12px; font-weight: 600; box-shadow: 0 10px 25px rgba(0,0,0,0.2); animation: fadeIn 0.3s ease; border-left: 5px solid var(--primary); }
    .toast-item.success { border-left-color: #10b981; }
    .toast-item.error { border-left-color: var(--primary); }
    .toast-item.warning { border-left-color: #f59e0b; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
  </style>
</head>
<body>

  <div class="bg-watermarks">
    <div class="watermark wm-1">Foodies.</div>
    <div class="watermark wm-2">Foodies.</div>
    <div class="watermark wm-3">Foodies.</div>
    <div class="watermark wm-4">Foodies.</div>
    <div class="watermark wm-5">Foodies.</div>
  </div>

  <div id="toast-container"></div>

  <div id="auth-wrapper" class="auth-container">
    <div id="login-screen" class="auth-card">
      <div class="brand-logo">Foodies<span>.</span></div>
      <div class="auth-subtitle">Sign in to experience fine fast delivery</div>
      <div class="form-group">
        <label>Email Address</label>
        <input type="email" id="login-email" placeholder="name@example.com">
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" id="login-password" placeholder="••••••••">
      </div>
      <button class="btn-primary" onclick="handleLogin()">Sign In</button>
      <div class="toggle-link" onclick="showAuthScreen('signup-screen')">Don't have an account? Register</div>
      <div class="toggle-link" onclick="showAuthScreen('forgot-screen')" style="margin-top: 8px; font-size: 11px;">Forgot password?</div>
    </div>

    <div id="signup-screen" class="auth-card hidden">
      <div class="brand-logo">Foodies<span>.</span></div>
      <div class="auth-subtitle">Create your personal account</div>
      <div class="form-group">
        <label>Email Address</label>
        <input type="email" id="signup-email" placeholder="name@example.com">
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" id="signup-password" placeholder="••••••••">
      </div>
      <div class="form-group">
        <label>6-Digit Security PIN</label>
        <input type="text" id="signup-pin" maxlength="6" placeholder="123456" style="text-align: center; letter-spacing: 2px;">
      </div>
      <button class="btn-primary" onclick="handleSignup()">Register Account</button>
      <div class="toggle-link" onclick="showAuthScreen('login-screen')">Back to Sign In</div>
    </div>

    <div id="verify-screen" class="auth-card hidden">
      <div class="brand-logo">Security<span>.</span></div>
      <div class="auth-subtitle">Enter your 6-digit PIN to authenticate</div>
      <div class="form-group">
        <input type="text" id="verify-pin" maxlength="6" placeholder="******" style="text-align: center; font-size: 18px; letter-spacing: 4px;">
      </div>
      <button class="btn-primary" onclick="handleVerifyPIN()">Confirm PIN</button>
    </div>

    <div id="forgot-screen" class="auth-card hidden">
      <div class="brand-logo">Reset<span>.</span></div>
      <div class="auth-subtitle">Enter your email for secure reset link</div>
      <div class="form-group">
        <label>Email Address</label>
        <input type="email" id="forgot-email" placeholder="name@example.com">
      </div>
      <button class="btn-primary" onclick="handleForgotPassword()">Send Reset Link</button>
      <div id="debug-reset-area" style="margin-top: 12px; font-size: 11px; word-break: break-all;"></div>
      <div class="toggle-link" onclick="showAuthScreen('login-screen')">Back to Sign In</div>
    </div>
  </div>

  <div id="menu-wrapper" class="app-container hidden">
    <nav>
      <div class="brand-logo">Foodies<span>.</span></div>
      <div class="nav-actions">
        <button class="btn-theme" onclick="toggleDarkMode()" title="Toggle Dark/Light Mode">🌓</button>
        <span id="live-visitors" class="user-badge" style="background:#dcfce7; color:#166534;">● Online: 1</span>
        <span id="user-display-email" class="user-badge"></span>
        <button id="nav-rider-btn" class="btn-nav-action btn-rider hidden" onclick="toggleRiderChatModal()">🛵 Track Rider</button>
        <button class="btn-nav-action btn-profile" onclick="openProfileModal()">👤 Profile</button>
        <button id="nav-admin-btn" class="btn-nav-action btn-admin hidden" onclick="openAdminModal()">⚙️ Admin</button>
        <button class="btn-nav-action btn-cart" onclick="openCartModal()">🛒 Cart <span id="cart-count-badge" class="btn-cart-count">0</span></button>
        <button class="btn-nav-action btn-logout" onclick="handleLogout()">Log Out</button>
      </div>
    </nav>

    <div class="hero-section">
      <h1 id="hero-title">Delicious Meals, Express Delivery</h1>
      <p id="hero-subtitle">Choose a restaurant to see what they're cooking</p>
      <div class="search-wrapper">
        <input type="text" id="searchInput" placeholder="Search restaurants by name or cuisine..." onkeyup="filterFoods()">
      </div>
    </div>

    <div id="restaurantHeader" class="restaurant-header hidden">
      <button class="btn-back" onclick="showRestaurantList()">← All Restaurants</button>
      <div class="restaurant-header-info">
        <span id="rh-logo" class="rh-logo"></span>
        <div>
          <div id="rh-name" class="rh-name"></div>
          <div id="rh-meta" class="rh-meta"></div>
        </div>
      </div>
    </div>

    <div class="categories-row hidden" id="categoryContainer"></div>

    <div class="restaurant-grid" id="restaurantGrid"></div>
    <div class="food-grid hidden" id="foodGrid"></div>
  </div>

  <div id="cartModal" class="modal-overlay hidden">
    <div class="modal-box">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
        <h3 style="font-size: 18px; font-weight: 800;">Your Order Basket</h3>
        <span style="cursor: pointer; font-weight: bold; font-size: 18px;" onclick="closeCartModal()">✕</span>
      </div>
      <div id="cart-restaurant-name" style="font-size: 12px; color: var(--text-muted); font-weight: 600; margin-bottom: 12px;"></div>
      <div id="cart-items-container" style="max-height: 200px; overflow-y: auto; margin-bottom: 16px; padding-right: 4px;"></div>
      <div style="display: flex; justify-content: space-between; font-weight: 800; font-size: 16px; margin-bottom: 16px; border-top: 1px solid var(--border); padding-top: 12px;">
        <span>Total:</span>
        <span id="cart-total-price" style="color: var(--primary);">₦0</span>
      </div>
      <div class="form-group">
        <label>Delivery Address</label>
        <input type="text" id="delivery-address" placeholder="e.g. Sauka new site gate, House 5">
      </div>
      <button class="btn-primary" id="checkout-btn" onclick="payWithPaystack()">Proceed to Checkout & Generate PDF Invoice</button>
    </div>
  </div>

  <div id="profileModal" class="modal-overlay hidden">
    <div class="modal-box">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 style="font-size: 18px; font-weight: 800;">User Account Profile</h3>
        <span style="cursor: pointer; font-weight: bold; font-size: 18px;" onclick="closeProfileModal()">✕</span>
      </div>
      <div class="form-group">
        <label>Phone Number</label>
        <input type="text" id="profile-phone" placeholder="+234 800 000 0000">
      </div>
      <div class="form-group">
        <label>Default Delivery Address</label>
        <textarea id="profile-address" rows="3" placeholder="Enter full delivery address"></textarea>
      </div>
      <button class="btn-primary" onclick="saveUserProfile()">Save Profile Details</button>

      <div class="cs-section">
        <h4>Customer Service</h4>
        <p>Need help with an order, refund, or delivery issue? Reach out directly:</p>
        <a class="cs-link" href="tel:+2349018793897">
          <span class="cs-icon">📞</span>
          <span>
            <span class="cs-label">Call / WhatsApp</span>
            <span class="cs-value">0901 879 3897</span>
          </span>
        </a>
        <a class="cs-link" href="mailto:timothyadi2008@gmail.com">
          <span class="cs-icon">✉️</span>
          <span>
            <span class="cs-label">Email</span>
            <span class="cs-value">timothyadi2008@gmail.com</span>
          </span>
        </a>
      </div>
    </div>
  </div>

  <div id="adminModal" class="modal-overlay hidden">
    <div class="modal-box" style="max-width: 500px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 style="font-size: 18px; font-weight: 800;">Administrative Dashboard</h3>
        <span style="cursor: pointer; font-weight: bold; font-size: 18px;" onclick="closeAdminModal()">✕</span>
      </div>
      <div id="admin-stats-container" style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px;"></div>
      <h4 style="font-size: 14px; font-weight: 700; margin-bottom: 8px;">Inventory & Menu Adjustments</h4>
      <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 14px;">Store managers can update stock availability or manage rider locations.</p>
      <button class="btn-primary" onclick="showToast('Inventory synced successfully', 'success')">Sync Store Data</button>
    </div>
  </div>

  <div id="chatModal" class="modal-overlay hidden">
    <div class="chat-box">
      <div class="chat-header">
        <div>
          <h4 style="font-size: 14px; font-weight: 700;">Delivery Dispatcher & Milestone Tracker</h4>
          <span id="live-rider-status" style="font-size: 11px; color: #10b981; font-weight: 600;">● In Transit</span>
        </div>
        <span style="cursor: pointer; font-weight: bold;" onclick="closeChatModal()">✕</span>
      </div>

      <div class="milestone-container" style="padding: 10px 20px; background: rgba(0,0,0,0.02);">
        <div class="milestone-step active" id="step-1"><div class="milestone-circle">✓</div>Order Placed</div>
        <div class="milestone-step active" id="step-2"><div class="milestone-circle">✓</div>Kitchen Prep</div>
        <div class="milestone-step active" id="step-3"><div class="milestone-circle">🚚</div>Out for Delivery</div>
        <div class="milestone-step" id="step-4"><div class="milestone-circle">4</div>Delivered</div>
      </div>

      <div id="rider-map"></div>

      <div class="chat-body" id="chatMessages"></div>
      <div id="typingIndicator" style="font-size: 11px; color: var(--text-muted); padding: 4px 20px;" class="hidden">Rider is typing...</div>

      <div class="chat-input-area">
        <input type="text" id="chatInput" placeholder="Type message..." onkeypress="handleChatKeyPress(event)">
        <button class="btn-primary" style="width: auto; padding: 0 18px;" onclick="sendChatMessage()">Send</button>
      </div>
    </div>
  </div>

  <div id="receiptModal" class="modal-overlay hidden">
    <div class="modal-box receipt-box">
      <div class="receipt-check">✓</div>
      <h3 style="font-size: 18px; font-weight: 800; text-align:center;">Order Confirmed!</h3>
      <p style="font-size: 12px; color: var(--text-muted); text-align:center; margin-top: 4px;">
        Your payment went through and your order is on its way.
      </p>

      <div style="margin: 18px 0; background: rgba(0,0,0,0.03); border-radius: 14px; padding: 14px;">
        <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:8px;">
          <span style="color:var(--text-muted);">Order ID</span>
          <span id="receipt-order-id" style="font-weight:700;"></span>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:8px;">
          <span style="color:var(--text-muted);">Restaurant</span>
          <span id="receipt-restaurant" style="font-weight:700;"></span>
        </div>
        <div id="receipt-items-list" style="font-size:12px; margin: 8px 0; display:flex; flex-direction:column; gap:4px;"></div>
        <div style="display:flex; justify-content:space-between; font-size:14px; font-weight:800; border-top: 1px solid var(--border); padding-top:8px; margin-top:8px;">
          <span>Total Paid</span>
          <span id="receipt-total" style="color: var(--primary);"></span>
        </div>
        <div style="font-size:11px; color:var(--text-muted); margin-top:8px;">
          Delivering to: <span id="receipt-address"></span>
        </div>
      </div>

      <button class="btn-primary" id="receipt-download-btn" onclick="downloadReceiptPDF()">⬇ Download PDF Receipt</button>
      <button class="btn-primary" style="background: linear-gradient(135deg, #10b981, #059669); margin-top: 8px;" onclick="closeReceiptModal(); toggleRiderChatModal();">🛵 Track My Rider</button>
      <div class="toggle-link" style="margin-top: 12px;" onclick="closeReceiptModal()">Close</div>
    </div>
  </div>

  <script>
    let activeUserEmail = "";
    let cart = [];
    let map, riderMarker;
    let pollRiderInterval;

    const PAYSTACK_PUBLIC_KEY = "pk_test_15c3892f5824f99266724433804c708899e1994f";
    const categories = ["Pizza", "Burgers", "Local", "Sides", "Drinks", "Desserts"];

    const specificEmojis = {
      "Pepperoni Special": "🍕", "Margherita Special": "🍕", "BBQ Chicken Special": "🍕", "Four Cheese Special": "🧀",
      "Classic Cheese Special": "🍔", "Double Smash Special": "🍔", "Bacon Deluxe Special": "🍔", "Crispy Chicken Special": "🍗",
      "Smokey Jollof Special": "🍛", "Suya Skewers Special": "🍢", "Egusi Special": "🍲", "Fried Rice Special": "🍚",
      "Crispy Fries Special": "🍟", "Onion Rings Special": "🧅", "Garlic Bread Special": "🥖", "Coleslaw Special": "🥗",
      "Iced Cola Special": "🥤", "Fresh Lemonade Special": "🍋", "Orange Juice Special": "🧃", "Vanilla Milkshake Special": "🥛",
      "Lava Cake Special": "🍫", "NY Cheesecake Special": "🍰", "Apple Pie Special": "🥧", "Ice Cream Sundae Special": "🍨"
    };

    const itemPrefixes = {
      Pizza: ["Pepperoni", "Margherita", "BBQ Chicken", "Four Cheese"],
      Burgers: ["Classic Cheese", "Double Smash", "Bacon Deluxe", "Crispy Chicken"],
      Local: ["Smokey Jollof", "Suya Skewers", "Egusi", "Fried Rice"],
      Sides: ["Crispy Fries", "Onion Rings", "Garlic Bread", "Coleslaw"],
      Drinks: ["Iced Cola", "Fresh Lemonade", "Orange Juice", "Vanilla Milkshake"],
      Desserts: ["Lava Cake", "NY Cheesecake", "Apple Pie", "Ice Cream Sundae"]
    };

    const masterCatalog = [];
    for (let i = 1; i <= 40; i++) {
      const category = categories[(i - 1) % categories.length];
      const prefixes = itemPrefixes[category] || ["Special"];
      const baseName = prefixes[(i - 1) % prefixes.length];
      const name = `${baseName} Special`;
      masterCatalog.push({
        id: i, name: name, category: category, price: ((i % 10) + 10) * 250,
        emoji: specificEmojis[name] || "🍽️", desc: `Freshly prepared ${name.toLowerCase()} made with premium ingredients.`
      });
    }

    // 20 restaurants, each specialising in a couple of categories from the master
    // catalog. Prices differ per restaurant via a price multiplier, so the same
    // dish costs a different amount depending on where you order it from.
    const restaurantDefs = [
      { name: "Pizza Palace",         cuisine: "Pizza",           categories: ["Pizza", "Sides", "Drinks"],     multiplier: 1.05, logo: "🍕" },
      { name: "Burger Barn",          cuisine: "Burgers",         categories: ["Burgers", "Sides", "Drinks"],   multiplier: 0.95, logo: "🍔" },
      { name: "Mama's Kitchen",       cuisine: "Local Dishes",    categories: ["Local", "Sides", "Drinks"],     multiplier: 1.00, logo: "🍛" },
      { name: "Cheesy Slice Co.",     cuisine: "Pizza",           categories: ["Pizza", "Desserts", "Drinks"],  multiplier: 1.15, logo: "🍕" },
      { name: "Grill Masters",        cuisine: "Burgers & Grill", categories: ["Burgers", "Local", "Drinks"],   multiplier: 1.10, logo: "🍔" },
      { name: "Naija Delight",        cuisine: "Local Dishes",    categories: ["Local", "Sides", "Desserts"],   multiplier: 0.90, logo: "🍲" },
      { name: "Crust & Co.",          cuisine: "Pizza",           categories: ["Pizza", "Sides"],               multiplier: 1.20, logo: "🍕" },
      { name: "Smash House",          cuisine: "Burgers",         categories: ["Burgers", "Desserts", "Drinks"],multiplier: 1.00, logo: "🍔" },
      { name: "Spice Route",          cuisine: "Local Dishes",    categories: ["Local", "Drinks", "Desserts"],  multiplier: 1.05, logo: "🍛" },
      { name: "Sweet Tooth Café",     cuisine: "Desserts",        categories: ["Desserts", "Drinks"],           multiplier: 0.95, logo: "🍰" },
      { name: "The Sizzle Spot",      cuisine: "Burgers & Grill", categories: ["Burgers", "Sides"],              multiplier: 1.25, logo: "🍔" },
      { name: "Golden Crust Pizzeria",cuisine: "Pizza",           categories: ["Pizza", "Drinks"],               multiplier: 0.90, logo: "🍕" },
      { name: "Local Flavors",        cuisine: "Local Dishes",    categories: ["Local", "Sides"],                multiplier: 1.00, logo: "🍲" },
      { name: "Frosty's Drinks & Ice",cuisine: "Drinks & Dessert",categories: ["Drinks", "Desserts"],            multiplier: 0.85, logo: "🥤" },
      { name: "Fry Zone",             cuisine: "Sides & Snacks",  categories: ["Sides", "Drinks"],               multiplier: 0.80, logo: "🍟" },
      { name: "Urban Bites",          cuisine: "Pizza & Burgers", categories: ["Burgers", "Pizza"],              multiplier: 1.10, logo: "🍔" },
      { name: "Taste of Naija",       cuisine: "Local Dishes",    categories: ["Local", "Desserts"],             multiplier: 1.15, logo: "🍛" },
      { name: "Quick Slice",          cuisine: "Pizza",           categories: ["Pizza", "Sides", "Drinks"],      multiplier: 0.95, logo: "🍕" },
      { name: "Chop House",           cuisine: "Local & Grill",   categories: ["Local", "Burgers"],              multiplier: 1.00, logo: "🍲" },
      { name: "Dessert Dreams",       cuisine: "Desserts",        categories: ["Desserts", "Sides"],             multiplier: 1.05, logo: "🍰" }
    ];

    const restaurants = restaurantDefs.map((def, idx) => {
      const id = `r${idx + 1}`;
      const menu = masterCatalog
        .filter(item => def.categories.includes(item.category))
        .map(item => ({
          ...item,
          cartItemId: `${id}::${item.id}`,
          price: Math.round((item.price * def.multiplier) / 50) * 50,
          restaurantId: id,
          restaurantName: def.name
        }));
      return {
        id, name: def.name, cuisine: def.cuisine, logo: def.logo,
        rating: (4 + ((idx * 37) % 10) / 10).toFixed(1),
        eta: `${20 + (idx % 4) * 5}-${35 + (idx % 4) * 5} min`,
        categories: def.categories,
        menu
      };
    });

    let currentView = 'restaurants'; // 'restaurants' | 'menu'
    let currentRestaurant = null;

    window.addEventListener('DOMContentLoaded', () => {
      const savedEmail = localStorage.getItem('foodies_user_email');
      const savedUsername = localStorage.getItem('foodies_user_username');
      const savedTheme = localStorage.getItem('foodies_theme') || 'light';
      document.documentElement.setAttribute('data-theme', savedTheme);

      if (savedEmail && savedUsername) {
        openMenu(savedEmail, savedUsername);
      } else {
        renderSkeletonLoaders();
      }
    });

    function toggleDarkMode() {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('foodies_theme', next);
      showToast(`Switched to ${next} mode`, 'info');
    }

    function showToast(msg, type = 'success') {
      const container = document.getElementById('toast-container');
      const toast = document.createElement('div');
      toast.className = `toast-item ${type}`;
      toast.innerText = msg;
      container.appendChild(toast);
      setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
      }, 3500);
    }

    function showAuthScreen(id) {
      document.querySelectorAll('.auth-container .auth-card').forEach(el => el.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
    }

    function renderSkeletonLoaders() {
      const grid = document.getElementById('restaurantGrid');
      grid.innerHTML = '';
      for (let i = 0; i < 8; i++) {
        grid.innerHTML += `
          <div class="skeleton-card">
            <div class="skeleton-line" style="height: 120px; width: 100%;"></div>
            <div class="skeleton-line" style="height: 16px; width: 80%;"></div>
            <div class="skeleton-line" style="height: 12px; width: 50%;"></div>
          </div>
        `;
      }
    }

    function openMenu(email, username) {
      activeUserEmail = email;
      localStorage.setItem('foodies_user_email', email);
      localStorage.setItem('foodies_user_username', username);

      document.getElementById('auth-wrapper').classList.add('hidden');
      document.getElementById('menu-wrapper').classList.remove('hidden');
      document.getElementById('user-display-email').innerText = username;

      if (email.includes("admin")) {
        document.getElementById('nav-admin-btn').classList.remove('hidden');
      }

      renderSkeletonLoaders();
      setTimeout(() => {
        showRestaurantList();
      }, 600);

      updateCartUI();
      startVisitorMonitoring();
    }

    async function handleSignup() {
      const email = document.getElementById('signup-email').value;
      const password = document.getElementById('signup-password').value;
      const pin = document.getElementById('signup-pin').value;

      const res = await fetch('/api/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, pin })
      });
      const data = await res.json();
      if (data.success) {
        showToast("Account created successfully with email verification!", "success");
        openMenu(data.email, data.username);
      } else {
        showToast(data.error, "error");
      }
    }

    async function handleLogin() {
      const email = document.getElementById('login-email').value;
      const password = document.getElementById('login-password').value;

      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      const data = await res.json();
      if (data.require_pin) {
        showAuthScreen('verify-screen');
      } else {
        showToast(data.error || "Invalid credentials", "error");
      }
    }

    async function handleVerifyPIN() {
      const pin = document.getElementById('verify-pin').value;
      const res = await fetch('/api/verify-pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin })
      });
      const data = await res.json();
      if (data.success) {
        showToast("PIN verified successfully", "success");
        openMenu(data.email, data.username);
      } else {
        showToast(data.error, "error");
      }
    }

    async function handleForgotPassword() {
      const email = document.getElementById('forgot-email').value;
      const res = await fetch('/api/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (data.success) {
        showToast("Password reset link generated!", "success");
        document.getElementById('debug-reset-area').innerHTML = `<b>Secure Link:</b> <a href="${data.debug_link}" target="_blank" style="color:var(--primary)">Click to Reset Password</a>`;
      } else {
        showToast(data.error, "error");
      }
    }

    async function handleLogout() {
      await fetch('/api/logout', { method: 'POST' });
      localStorage.removeItem('foodies_user_email');
      localStorage.removeItem('foodies_user_username');
      cart = [];
      document.getElementById('nav-rider-btn').classList.add('hidden');
      document.getElementById('menu-wrapper').classList.add('hidden');
      document.getElementById('auth-wrapper').classList.remove('hidden');
      showAuthScreen('login-screen');
      showToast("Logged out successfully", "info");
    }

    function renderRestaurants(list) {
      const grid = document.getElementById('restaurantGrid');
      grid.innerHTML = '';
      if (list.length === 0) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; color:var(--text-muted); padding:30px;">No restaurants match your search.</div>`;
        return;
      }
      list.forEach(r => {
        const card = document.createElement('div');
        card.className = 'restaurant-card';
        card.onclick = () => openRestaurant(r.id);
        card.innerHTML = `
          <div class="restaurant-card-top">
            <div class="restaurant-logo">${r.logo}</div>
            <div>
              <div class="restaurant-name">${r.name}</div>
              <div class="restaurant-cuisine">${r.cuisine}</div>
            </div>
          </div>
          <div class="restaurant-meta">
            <span class="rating">★ ${r.rating}</span>
            <span>${r.eta}</span>
            <span>${r.menu.length} items</span>
          </div>
          <button class="restaurant-view-btn">View Menu</button>
        `;
        grid.appendChild(card);
      });
    }

    function showRestaurantList() {
      currentView = 'restaurants';
      currentRestaurant = null;
      document.getElementById('hero-title').innerText = 'Delicious Meals, Express Delivery';
      document.getElementById('hero-subtitle').innerText = "Choose a restaurant to see what they're cooking";
      document.getElementById('searchInput').placeholder = 'Search restaurants by name or cuisine...';
      document.getElementById('searchInput').value = '';
      document.getElementById('restaurantHeader').classList.add('hidden');
      document.getElementById('categoryContainer').classList.add('hidden');
      document.getElementById('foodGrid').classList.add('hidden');
      document.getElementById('restaurantGrid').classList.remove('hidden');
      renderRestaurants(restaurants);
    }

    function openRestaurant(id) {
      const restaurant = restaurants.find(r => r.id === id);
      if (!restaurant) return;
      currentView = 'menu';
      currentRestaurant = restaurant;

      document.getElementById('restaurantGrid').classList.add('hidden');
      document.getElementById('foodGrid').classList.remove('hidden');
      document.getElementById('categoryContainer').classList.remove('hidden');
      document.getElementById('restaurantHeader').classList.remove('hidden');
      document.getElementById('hero-title').innerText = restaurant.name;
      document.getElementById('hero-subtitle').innerText = restaurant.cuisine;
      document.getElementById('searchInput').placeholder = `Search ${restaurant.name}'s menu...`;
      document.getElementById('searchInput').value = '';

      document.getElementById('rh-logo').innerText = restaurant.logo;
      document.getElementById('rh-name').innerText = restaurant.name;
      document.getElementById('rh-meta').innerText = `★ ${restaurant.rating}  •  ${restaurant.eta}  •  ${restaurant.cuisine}`;

      renderCategoryPills(restaurant.categories);
      renderFoods(restaurant.menu);
    }

    function renderCategoryPills(cats) {
      const container = document.getElementById('categoryContainer');
      container.innerHTML = `<div class="category-pill active" onclick="filterCategory('All')">All Items</div>`;
      cats.forEach(c => {
        container.innerHTML += `<div class="category-pill" onclick="filterCategory('${c}')">${c}</div>`;
      });
    }

    function renderFoods(items) {
      const grid = document.getElementById('foodGrid');
      grid.innerHTML = '';
      if (items.length === 0) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; color:var(--text-muted); padding:30px;">No dishes match your search.</div>`;
        return;
      }
      items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'food-card';
        card.innerHTML = `
          <div>
            <div class="food-img-frame">${item.emoji}</div>
            <div class="food-title">${item.name}</div>
            <div class="food-desc">${item.desc}</div>
          </div>
          <div class="food-footer">
            <span class="food-price">₦${item.price.toLocaleString()}</span>
            <button class="btn-add" onclick="addToCart('${item.cartItemId}')">+ Add</button>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function filterCategory(category) {
      document.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
      event.target.classList.add('active');
      if (!currentRestaurant) return;
      if (category === 'All') {
        renderFoods(currentRestaurant.menu);
      } else {
        renderFoods(currentRestaurant.menu.filter(i => i.category === category));
      }
    }

    function filterFoods() {
      const query = document.getElementById('searchInput').value.toLowerCase();
      if (currentView === 'restaurants') {
        renderRestaurants(restaurants.filter(r =>
          r.name.toLowerCase().includes(query) || r.cuisine.toLowerCase().includes(query)
        ));
      } else if (currentRestaurant) {
        renderFoods(currentRestaurant.menu.filter(i =>
          i.name.toLowerCase().includes(query) || i.desc.toLowerCase().includes(query)
        ));
      }
    }

    function addToCart(cartItemId) {
      const item = currentRestaurant.menu.find(f => f.cartItemId === cartItemId);
      if (!item) return;

      if (cart.length > 0 && cart[0].restaurantId !== item.restaurantId) {
        const confirmed = confirm(
          `Your cart has items from ${cart[0].restaurantName}. Adding from ${item.restaurantName} will clear your current cart. Continue?`
        );
        if (!confirmed) return;
        cart = [];
      }

      cart.push({ ...item, cartInstanceId: Date.now() + Math.random() });
      updateCartUI();
      showToast(`${item.name} added to cart`, "success");
    }

    function removeFromCart(cartInstanceId) {
      const index = cart.findIndex(item => item.cartInstanceId === cartInstanceId);
      if (index !== -1) {
        cart.splice(index, 1);
        openCartModal();
        updateCartUI();
        showToast("Item removed from cart", "warning");
      }
    }

    function updateCartUI() {
      document.getElementById('cart-count-badge').innerText = cart.length;
    }

    function openCartModal() {
      const container = document.getElementById('cart-items-container');
      const restaurantLabel = document.getElementById('cart-restaurant-name');
      container.innerHTML = '';
      restaurantLabel.innerText = cart.length > 0 ? `From ${cart[0].restaurantName}` : '';
      let total = 0;
      if (cart.length === 0) {
        container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 20px;">Your cart is empty</div>`;
      } else {
        cart.forEach((item) => {
          total += item.price;
          container.innerHTML += `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px;">
              <span>${item.emoji} ${item.name} - ₦${item.price.toLocaleString()}</span>
              <span style="color: var(--primary); cursor: pointer; font-weight: bold;" onclick="removeFromCart(${item.cartInstanceId})">✕</span>
            </div>
          `;
        });
      }
      document.getElementById('cart-total-price').innerText = `₦${total.toLocaleString()}`;
      document.getElementById('cartModal').classList.remove('hidden');
    }

    function closeCartModal() { document.getElementById('cartModal').classList.add('hidden'); }

    async function openProfileModal() {
      const res = await fetch('/api/get-profile');
      const data = await res.json();
      if (data.success) {
        document.getElementById('profile-phone').value = data.phone || '';
        document.getElementById('profile-address').value = data.address || '';
      }
      document.getElementById('profileModal').classList.remove('hidden');
    }

    function closeProfileModal() { document.getElementById('profileModal').classList.add('hidden'); }

    async function saveUserProfile() {
      const phone = document.getElementById('profile-phone').value;
      const address = document.getElementById('profile-address').value;
      const res = await fetch('/api/update-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, address })
      });
      const data = await res.json();
      if (data.success) {
        showToast("Profile details updated successfully!", "success");
        closeProfileModal();
      } else {
        showToast("Failed to save profile", "error");
      }
    }

    async function openAdminModal() {
      document.getElementById('adminModal').classList.remove('hidden');
      const res = await fetch('/api/admin/stats');
      const data = await res.json();
      if (data.success) {
        document.getElementById('admin-stats-container').innerHTML = `
          <div style="background: rgba(0,0,0,0.03); padding: 14px; border-radius: 12px; text-align:center;">
            <div style="font-size: 11px; color: var(--text-muted);">Total Revenue</div>
            <div style="font-size: 16px; font-weight: 800; color: var(--primary);">₦${data.revenue.toLocaleString()}</div>
          </div>
          <div style="background: rgba(0,0,0,0.03); padding: 14px; border-radius: 12px; text-align:center;">
            <div style="font-size: 11px; color: var(--text-muted);">Total Orders</div>
            <div style="font-size: 16px; font-weight: 800;">${data.orders_count}</div>
          </div>
          <div style="background: rgba(0,0,0,0.03); padding: 14px; border-radius: 12px; text-align:center;">
            <div style="font-size: 11px; color: var(--text-muted);">Registered Users</div>
            <div style="font-size: 16px; font-weight: 800;">${data.users_count}</div>
          </div>
          <div style="background: rgba(0,0,0,0.03); padding: 14px; border-radius: 12px; text-align:center;">
            <div style="font-size: 11px; color: var(--text-muted);">Active Visitors</div>
            <div style="font-size: 16px; font-weight: 800; color: #10b981;">${data.active_visitors}</div>
          </div>
        `;
      }
    }
    function closeAdminModal() { document.getElementById('adminModal').classList.add('hidden'); }

    let lastOrder = null;

    function payWithPaystack() {
      if (cart.length === 0) {
        showToast("Your cart is empty!", "error");
        return;
      }
      const address = document.getElementById('delivery-address').value.trim();
      if (!address) {
        showToast("Please enter a delivery address", "warning");
        return;
      }
      if (typeof PaystackPop === 'undefined') {
        showToast("Payment provider failed to load. Check your connection and reload the page.", "error");
        return;
      }
      if (!activeUserEmail) {
        showToast("You must be logged in to checkout.", "error");
        return;
      }

      const checkoutBtn = document.getElementById('checkout-btn');
      let totalAmount = cart.reduce((sum, item) => sum + item.price, 0);
      if (!totalAmount || totalAmount <= 0) {
        showToast("Cart total must be greater than zero.", "error");
        return;
      }

      try {
        let handler = PaystackPop.setup({
          key: PAYSTACK_PUBLIC_KEY,
          email: activeUserEmail,
          amount: Math.round(totalAmount * 100),
          currency: 'NGN',
          ref: `FOODIES-${Date.now()}`,
          callback: function(response) {
            // Paystack's callback must stay synchronous-friendly; kick off our async work
            // right away but don't make the user wait on a popup that browsers may block.
            finalizeOrderAfterPayment(address, checkoutBtn);
          },
          onClose: function() {
            showToast("Payment window closed.", "info");
          }
        });
        handler.openIframe();
      } catch (err) {
        console.error(err);
        showToast("Could not open the payment window. Please reload and try again.", "error");
      }
    }

    async function finalizeOrderAfterPayment(address, checkoutBtn) {
      checkoutBtn.disabled = true;
      checkoutBtn.innerText = "Finalizing order...";
      const restaurantName = cart.length > 0 ? cart[0].restaurantName : '';
      try {
        const checkoutRes = await fetch('/api/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: cart, address: address, restaurant: restaurantName })
        });
        const checkoutData = await checkoutRes.json();

        if (!checkoutRes.ok || !checkoutData.success) {
          showToast(checkoutData.error || "Payment succeeded but saving the order failed. Contact support with your payment reference.", "error");
          return;
        }

        showToast("Payment successful!", "success");
        closeCartModal();
        openReceiptModal({
          order_id: checkoutData.order_id,
          items: [...cart],
          total: checkoutData.total,
          address: address,
          restaurant: restaurantName
        });

        cart = [];
        updateCartUI();
        document.getElementById('nav-rider-btn').classList.remove('hidden');
      } catch (err) {
        console.error(err);
        showToast("Something went wrong finalizing your order. Please contact support.", "error");
      } finally {
        checkoutBtn.disabled = false;
        checkoutBtn.innerText = "Proceed to Checkout & Generate PDF Invoice";
      }
    }

    function openReceiptModal(order) {
      lastOrder = order;
      document.getElementById('receipt-order-id').innerText = order.order_id;
      document.getElementById('receipt-total').innerText = `₦${Number(order.total).toLocaleString()}`;
      document.getElementById('receipt-address').innerText = order.address;
      document.getElementById('receipt-restaurant').innerText = order.restaurant || '';

      const list = document.getElementById('receipt-items-list');
      list.innerHTML = '';
      order.items.forEach(item => {
        list.innerHTML += `
          <div style="display:flex; justify-content:space-between;">
            <span>${item.emoji || ''} ${item.name}</span>
            <span>₦${Number(item.price).toLocaleString()}</span>
          </div>
        `;
      });

      document.getElementById('receiptModal').classList.remove('hidden');
    }

    function closeReceiptModal() {
      document.getElementById('receiptModal').classList.add('hidden');
    }

    function downloadReceiptPDF() {
      // Triggered directly by a button click (a genuine user gesture), so it
      // won't be treated as an unsolicited popup — unlike opening it automatically
      // inside an async callback, which browsers commonly block silently.
      if (!lastOrder) {
        showToast("No receipt available yet.", "error");
        return;
      }
      window.location.href = `/api/download-receipt?order_id=${encodeURIComponent(lastOrder.order_id)}`;
    }

    async function toggleRiderChatModal() {
      document.getElementById('chatModal').classList.remove('hidden');
      initMap();
      if (!pollRiderInterval) {
        pollRiderInterval = setInterval(fetchRiderLocation, 3000);
      }
      await loadPersistentChatHistory();
    }

    function closeChatModal() {
      document.getElementById('chatModal').classList.add('hidden');
      if (pollRiderInterval) {
        clearInterval(pollRiderInterval);
        pollRiderInterval = null;
      }
    }

    function initMap() {
      if (!map) {
        map = L.map('rider-map').setView([9.05785, 7.49508], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
        riderMarker = L.marker([9.05785, 7.49508]).addTo(map).bindPopup("<b>Delivery Rider</b><br/>In Transit").openPopup();
      } else {
        setTimeout(() => map.invalidateSize(), 200);
      }
    }

    async function fetchRiderLocation() {
      try {
        const res = await fetch('/api/rider-location');
        const data = await res.json();
        if (riderMarker && map) {
          const newLatLng = [data.lat, data.lng];
          riderMarker.setLatLng(newLatLng);
          map.panTo(newLatLng);
          document.getElementById('live-rider-status').innerText = `● ${data.status}`;
        }
      } catch (e) { console.error("Location poll failed"); }
    }

    async function loadPersistentChatHistory() {
      const chatBody = document.getElementById('chatMessages');
      chatBody.innerHTML = '';
      try {
        const res = await fetch('/api/chat/history');
        const history = await res.json();
        if (history.length === 0) {
          appendChatMessage("Hello! I've picked up your order and I'm currently heading to your location.", "rider");
        } else {
          history.forEach(msg => appendChatMessage(msg.message, msg.sender));
        }
      } catch(e) {
        appendChatMessage("Hello! I've picked up your order and I'm currently heading to your location.", "rider");
      }
    }

    function appendChatMessage(text, sender) {
      const chatBody = document.getElementById('chatMessages');
      const msgDiv = document.createElement('div');
      msgDiv.className = `chat-msg ${sender}`;
      msgDiv.innerText = text;
      chatBody.appendChild(msgDiv);
      chatBody.scrollTop = chatBody.scrollHeight;
    }

    function handleChatKeyPress(e) { if (e.key === 'Enter') sendChatMessage(); }

    async function sendChatMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;

      appendChatMessage(text, 'user');
      input.value = '';
      document.getElementById('typingIndicator').classList.remove('hidden');

      try {
        const res = await fetch('/api/rider-chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        document.getElementById('typingIndicator').classList.add('hidden');
        if (data.reply) appendChatMessage(data.reply, 'rider');
      } catch (e) {
        document.getElementById('typingIndicator').classList.add('hidden');
        appendChatMessage("Sorry, connection error.", 'rider');
      }
    }

    function startVisitorMonitoring() {
      setInterval(() => {
        const count = Math.floor(Math.random() * 5) + 12;
        const badge = document.getElementById('live-visitors');
        if (badge) badge.innerText = `● Online: ${count}`;
      }, 5000);
    }
  </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, port=5000)
