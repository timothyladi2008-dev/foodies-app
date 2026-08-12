import sqlite3
import time
import os
from flask import Flask, jsonify, request, session, render_template_string

app = Flask(__name__)
# Use a permanent hardcoded secret key so session tokens survive server reloads or restarts!
app.secret_key = "foodies_permanent_secure_session_key_2026"
DB_FILE = "users.db"

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
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users
            (
                email TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                pin TEXT NOT NULL
            )
        ''')
        conn.commit()


init_db()


def get_user(email):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, pin FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return {"email": row[0], "password": row[1], "pin": row[2]}
    return None


def save_user(email, password, pin):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (email, password, pin) VALUES (?, ?, ?)", (email, password, pin))
        conn.commit()


def extract_username(email):
    return email.split('@')[0] if email and '@' in email else email


@app.before_request
def track_visitors():
    active_visitors[request.remote_addr] = time.time()


# ---------------------------------------------------------
# FRONTEND TEMPLATE (HTML + JS + PERSISTENT LOCALSTORAGE LOGIN)
# ---------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
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
      --card-bg: rgba(255, 255, 255, 0.75);
      --border: rgba(255, 255, 255, 0.6);
      --glass-shadow: 0 20px 40px rgba(0, 0, 0, 0.08);
    }

    * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }

    body { 
      min-height: 100vh; 
      background: 
        radial-gradient(circle at 20% 20%, rgba(255, 255, 255, 0.6) 0%, transparent 40%),
        radial-gradient(circle at 80% 80%, rgba(255, 255, 255, 0.5) 0%, transparent 40%),
        linear-gradient(180deg, #38bdf8 0%, #7dd3fc 35%, #bae6fd 70%, #e0f2fe 100%); 
      background-attachment: fixed; 
      color: #1e293b; 
      position: relative; 
      overflow-x: hidden;
    }

    .bg-watermarks {
      position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
      pointer-events: none; z-index: 0; overflow: hidden;
    }

    .watermark {
      position: absolute; font-size: clamp(3rem, 6vw, 7rem); font-weight: 900;
      color: rgba(255, 255, 255, 0.22); text-transform: uppercase; letter-spacing: -2px; user-select: none;
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
      background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(20px); 
      border-radius: 24px; border: 1px solid var(--border); box-shadow: var(--glass-shadow); 
      padding: 40px 32px; width: 100%; max-width: 380px; text-align: center; 
    }

    .brand-logo { font-size: 26px; font-weight: 800; color: var(--dark); letter-spacing: -0.5px; }
    .brand-logo span { color: var(--primary); }

    .auth-subtitle { font-size: 13px; color: #64748b; margin-top: 4px; margin-bottom: 24px; }

    .form-group { margin-bottom: 14px; text-align: left; }
    .form-group label { font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; display: block; }

    input { 
      width: 100%; padding: 12px 16px; border-radius: 12px; border: 1px solid #cbd5e1; 
      background: white; outline: none; font-size: 13px; transition: all 0.2s ease; 
    }

    input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(255, 71, 87, 0.15); }

    .btn-primary { 
      width: 100%; padding: 12px; font-weight: 700; font-size: 13px; 
      background: linear-gradient(135deg, var(--primary), #ff6348); 
      color: white; border: none; border-radius: 12px; cursor: pointer; 
      transition: all 0.2s; box-shadow: 0 4px 12px rgba(255, 71, 87, 0.25);
    }

    .btn-primary:hover { opacity: 0.95; transform: translateY(-1px); }

    .toggle-link { margin-top: 16px; font-size: 12px; color: var(--dark); cursor: pointer; font-weight: 600; text-decoration: underline; }

    .app-container { max-width: 1280px; margin: 0 auto; padding: 24px; position: relative; z-index: 1; }

    nav { 
      display: flex; justify-content: space-between; align-items: center; 
      padding: 16px 28px; background: rgba(255, 255, 255, 0.8); backdrop-filter: blur(16px); 
      border-radius: 20px; border: 1px solid var(--border); box-shadow: var(--glass-shadow); margin-bottom: 30px; 
    }

    .nav-actions { display: flex; gap: 12px; align-items: center; }

    .user-badge { 
      font-size: 13px; font-weight: 600; background: rgba(15, 23, 42, 0.05); 
      padding: 8px 14px; border-radius: 10px; color: var(--dark); 
    }

    .btn-nav-action { 
      padding: 8px 16px; border-radius: 10px; font-size: 12px; font-weight: 700; 
      cursor: pointer; border: none; display: flex; align-items: center; gap: 6px; transition: all 0.2s; 
    }

    .btn-cart { background: var(--primary); color: white; box-shadow: 0 4px 12px rgba(255, 71, 87, 0.2); }
    .btn-cart-count { background: white; color: var(--primary); padding: 2px 7px; border-radius: 20px; font-size: 11px; }
    .btn-rider { background: #10b981; color: white; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.2); }
    .btn-logout { background: #e2e8f0; color: #475569; }

    .hero-section { text-align: center; margin: 20px 0 32px; }
    .hero-section h1 { font-size: 32px; font-weight: 800; color: var(--dark); letter-spacing: -1px; }
    .hero-section p { font-size: 14px; color: #334155; margin-top: 4px; font-weight: 500; }

    .search-wrapper { max-width: 550px; margin: 20px auto 0; position: relative; }
    .search-wrapper input { padding-left: 20px; height: 50px; border-radius: 30px; border: 1px solid rgba(255,255,255,0.9); background: rgba(255, 255, 255, 0.9); box-shadow: var(--glass-shadow); font-size: 14px; }

    .categories-row { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 10px; margin-bottom: 30px; }
    .category-pill { 
      background: rgba(255, 255, 255, 0.85); padding: 10px 20px; border-radius: 40px; 
      border: 1px solid var(--border); cursor: pointer; font-size: 13px; font-weight: 600; 
      color: #475569; white-space: nowrap; transition: all 0.2s; 
    }

    .category-pill.active { background: var(--dark); color: white; border-color: var(--dark); box-shadow: 0 4px 12px rgba(15, 23, 42, 0.15); }

    .food-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }

    .food-card { 
      background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px); border-radius: 20px; 
      border: 1px solid var(--border); padding: 16px; display: flex; flex-direction: column; 
      justify-content: space-between; transition: transform 0.2s, box-shadow 0.2s; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.03); 
    }

    .food-card:hover { transform: translateY(-4px); box-shadow: var(--glass-shadow); }

    .food-img-frame { 
      width: 100%; height: 120px; border-radius: 14px; overflow: hidden; margin-bottom: 12px; 
      background: #f1f5f9; display: flex; align-items: center; justify-content: center; font-size: 52px;
      transition: transform 0.3s ease;
    }
    .food-card:hover .food-img-frame { transform: scale(1.05); }

    .food-title { font-size: 15px; font-weight: 700; color: var(--dark); }
    .food-desc { font-size: 11px; color: #64748b; margin-top: 4px; height: 32px; overflow: hidden; line-height: 1.4; }

    .food-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; }
    .food-price { font-size: 15px; font-weight: 800; color: var(--primary); }
    .btn-add { background: var(--dark); color: white; border: none; padding: 8px 14px; border-radius: 10px; font-size: 11px; font-weight: 700; cursor: pointer; }

    .modal-overlay { 
      position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
      background: rgba(15, 23, 42, 0.4); backdrop-filter: blur(8px); 
      display: flex; justify-content: center; align-items: center; z-index: 100; padding: 20px; 
    }

    .modal-box { background: white; border-radius: 24px; padding: 28px; width: 100%; max-width: 440px; box-shadow: 0 25px 50px rgba(0,0,0,0.2); }

    .chat-box { 
      background: #ffffff; border-radius: 24px; width: 100%; max-width: 460px; height: 580px; 
      display: flex; flex-direction: column; box-shadow: 0 25px 50px rgba(0,0,0,0.2); overflow: hidden; 
    }

    .chat-header { padding: 16px 20px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; }

    #rider-map { height: 180px; width: 100%; border-bottom: 1px solid #e2e8f0; }

    .chat-body { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 10px; background: #fafafa; }

    .chat-msg { max-width: 80%; padding: 10px 14px; border-radius: 16px; font-size: 12px; line-height: 1.5; }
    .chat-msg.rider { background: white; border: 1px solid #e2e8f0; color: #1e293b; align-self: flex-start; border-bottom-left-radius: 2px; }
    .chat-msg.user { background: var(--dark); color: white; align-self: flex-end; border-bottom-right-radius: 2px; }

    .chat-input-area { padding: 14px 18px; border-top: 1px solid #e2e8f0; background: white; display: flex; gap: 8px; }

    #toast {
      position: fixed; bottom: 20px; right: 20px; background: var(--dark); color: white; 
      padding: 12px 20px; border-radius: 12px; font-size: 12px; font-weight: 600; 
      box-shadow: 0 10px 25px rgba(0,0,0,0.15); z-index: 200; transition: opacity 0.3s, transform 0.3s;
    }
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

  <div id="toast" class="hidden">Notification message</div>

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
  </div>

  <div id="menu-wrapper" class="app-container hidden">
    <nav>
      <div class="brand-logo">Foodies<span>.</span></div>
      <div class="nav-actions">
        <span id="live-visitors" class="user-badge" style="background:#dcfce7; color:#166534;">● Online: 1</span>
        <span id="user-display-email" class="user-badge"></span>
        <button id="nav-rider-btn" class="btn-nav-action btn-rider hidden" onclick="toggleRiderChatModal()">🛵 Track Rider</button>
        <button class="btn-nav-action btn-cart" onclick="openCartModal()">🛒 Cart <span id="cart-count-badge" class="btn-cart-count">0</span></button>
        <button class="btn-nav-action btn-logout" onclick="handleLogout()">Log Out</button>
      </div>
    </nav>

    <div class="hero-section">
      <h1>Delicious Meals, Express Delivery</h1>
      <p>Select from our curated menu cooked by top chefs</p>
      <div class="search-wrapper">
        <input type="text" id="searchInput" placeholder="Search 100+ dishes, drinks, local specials..." onkeyup="filterFoods()">
      </div>
    </div>

    <div class="categories-row" id="categoryContainer">
      <div class="category-pill active" onclick="filterCategory('All')">All Items</div>
      <div class="category-pill" onclick="filterCategory('Pizza')">Pizza</div>
      <div class="category-pill" onclick="filterCategory('Burgers')">Burgers</div>
      <div class="category-pill" onclick="filterCategory('Local')">Local Specials</div>
      <div class="category-pill" onclick="filterCategory('Sides')">Sides</div>
      <div class="category-pill" onclick="filterCategory('Drinks')">Drinks</div>
      <div class="category-pill" onclick="filterCategory('Desserts')">Desserts</div>
    </div>

    <div class="food-grid" id="foodGrid"></div>
  </div>

  <div id="cartModal" class="modal-overlay hidden">
    <div class="modal-box">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 style="font-size: 18px; font-weight: 800;">Your Order Basket</h3>
        <span style="cursor: pointer; font-weight: bold; font-size: 18px;" onclick="closeCartModal()">✕</span>
      </div>

      <div id="cart-items-container" style="max-height: 200px; overflow-y: auto; margin-bottom: 16px; padding-right: 4px;"></div>

      <div style="display: flex; justify-content: space-between; font-weight: 800; font-size: 16px; margin-bottom: 16px; border-top: 1px solid #e2e8f0; padding-top: 12px;">
        <span>Total:</span>
        <span id="cart-total-price" style="color: var(--primary);">₦0</span>
      </div>

      <div class="form-group">
        <label>Delivery Address</label>
        <input type="text" id="delivery-address" placeholder="e.g. Sauka new site gate, House 5">
      </div>

      <button class="btn-primary" onclick="payWithPaystack()">Proceed to Checkout</button>
    </div>
  </div>

  <div id="chatModal" class="modal-overlay hidden">
    <div class="chat-box">
      <div class="chat-header">
        <div>
          <h4 style="font-size: 14px; font-weight: 700;">Delivery Dispatcher</h4>
          <span id="live-rider-status" style="font-size: 11px; color: #10b981; font-weight: 600;">● In Transit</span>
        </div>
        <span style="cursor: pointer; font-weight: bold;" onclick="closeChatModal()">✕</span>
      </div>

      <div id="rider-map"></div>

      <div class="chat-body" id="chatMessages"></div>
      <div id="typingIndicator" style="font-size: 11px; color: #64748b; padding: 4px 20px;" class="hidden">Rider is typing...</div>

      <div class="chat-input-area">
        <input type="text" id="chatInput" placeholder="Type message..." onkeypress="handleChatKeyPress(event)">
        <button class="btn-primary" style="width: auto; padding: 0 18px;" onclick="sendChatMessage()">Send</button>
      </div>
    </div>
  </div>

  <script>
    let activeUserEmail = "";
    let cart = [];
    let map, riderMarker;
    let pollRiderInterval;

    const PAYSTACK_PUBLIC_KEY = "pk_test_15c3892f5824f99266724433804c708899e1994f";

    const categories = ["Pizza", "Burgers", "Local", "Sides", "Drinks", "Desserts"];

    const preciseEmojiMap = {
      // Pizza
      "Pepperoni Special": "🍕", "Pepperoni Combo 2": "🍕", "Pepperoni Combo 3": "🍕", "Pepperoni Combo 4": "🍕",
      "Margherita Special": "🍕", "Margherita Combo 2": "🍕", "Margherita Combo 3": "🍕", "Margherita Combo 4": "🍕",
      "BBQ Chicken Special": "🍕", "BBQ Chicken Combo 2": "🍕", "BBQ Chicken Combo 3": "🍕", "BBQ Chicken Combo 4": "🍕",
      "Four Cheese Special": "🧀", "Four Cheese Combo 2": "🧀", "Four Cheese Combo 3": "🧀", "Four Cheese Combo 4": "🧀",
      "Hawaiian Special": "🍍", "Hawaiian Combo 2": "🍍", "Hawaiian Combo 3": "🍍", "Hawaiian Combo 4": "🍍",
      "Veggie Delight Special": "🥦", "Veggie Delight Combo 2": "🥦", "Veggie Delight Combo 3": "🥦", "Veggie Delight Combo 4": "🥦",
      "Meat Feast Special": "🥩", "Meat Feast Combo 2": "🥩", "Meat Feast Combo 3": "🥩", "Meat Feast Combo 4": "🥩",
      "Truffle Mushroom Special": "🍄", "Truffle Mushroom Combo 2": "🍄", "Truffle Mushroom Combo 3": "🍄", "Truffle Mushroom Combo 4": "🍄",

      // Burgers
      "Classic Cheese Special": "🍔", "Classic Cheese Combo 2": "🍔", "Classic Cheese Combo 3": "🍔", "Classic Cheese Combo 4": "🍔",
      "Double Smash Special": "🍔", "Double Smash Combo 2": "🍔", "Double Smash Combo 3": "🍔", "Double Smash Combo 4": "🍔",
      "Bacon Deluxe Special": "🥓", "Bacon Deluxe Combo 2": "🥓", "Bacon Deluxe Combo 3": "🥓", "Bacon Deluxe Combo 4": "🥓",
      "Crispy Chicken Special": "🍗", "Crispy Chicken Combo 2": "🍗", "Crispy Chicken Combo 3": "🍗", "Crispy Chicken Combo 4": "🍗",
      "Veggie Beyond Special": "🥗", "Veggie Beyond Combo 2": "🥗", "Veggie Beyond Combo 3": "🥗", "Veggie Beyond Combo 4": "🥗",
      "Mushroom Swiss Special": "🍄", "Mushroom Swiss Combo 2": "🍄", "Mushroom Swiss Combo 3": "🍄", "Mushroom Swiss Combo 4": "🍄",
      "Spicy Zinger Special": "🌶️", "Spicy Zinger Combo 2": "🌶️", "Spicy Zinger Combo 3": "🌶️", "Spicy Zinger Combo 4": "🌶️",
      "Avocado Beef Special": "🥑", "Avocado Beef Combo 2": "🥑", "Avocado Beef Combo 3": "🥑", "Avocado Beef Combo 4": "🥑",

      // Local
      "Smokey Jollof Special": "🍛", "Smokey Jollof Combo 2": "🍛", "Smokey Jollof Combo 3": "🍛", "Smokey Jollof Combo 4": "🍛",
      "Suya Skewers Special": "🍢", "Suya Skewers Combo 2": "🍢", "Suya Skewers Combo 3": "🍢", "Suya Skewers Combo 4": "🍢",
      "Egusi Special Special": "🍲", "Egusi Special Combo 2": "🍲", "Egusi Special Combo 3": "🍲", "Egusi Special Combo 4": "🍲",
      "Fried Rice Special": "🍛", "Fried Rice Combo 2": "🍛", "Fried Rice Combo 3": "🍛", "Fried Rice Combo 4": "🍛",
      "Ofada Delicacy Special": "🍚", "Ofada Delicacy Combo 2": "🍚", "Ofada Delicacy Combo 3": "🍚", "Ofada Delicacy Combo 4": "🍚",
      "Spicy Asun Special": "🍖", "Spicy Asun Combo 2": "🍖", "Spicy Asun Combo 3": "🍖", "Spicy Asun Combo 4": "🍖",
      "Fisherman Soup Special": "🍲", "Fisherman Soup Combo 2": "🍲", "Fisherman Soup Combo 3": "🍲", "Fisherman Soup Combo 4": "🍲",
      "Pepper Soup Special": "🥣", "Pepper Soup Combo 2": "🥣", "Pepper Soup Combo 3": "🥣", "Pepper Soup Combo 4": "🥣",

      // Sides
      "Crispy Fries Special": "🍟", "Crispy Fries Combo 2": "🍟", "Crispy Fries Combo 3": "🍟", "Crispy Fries Combo 4": "🍟",
      "Onion Rings Special": "🧅", "Onion Rings Combo 2": "🧅", "Onion Rings Combo 3": "🧅", "Onion Rings Combo 4": "🧅",
      "Garlic Bread Special": "🍞", "Garlic Bread Combo 2": "🍞", "Garlic Bread Combo 3": "🍞", "Garlic Bread Combo 4": "🍞",
      "Coleslaw Special": "🥗", "Coleslaw Combo 2": "🥗", "Coleslaw Combo 3": "🥗", "Coleslaw Combo 4": "🥗",
      "Mozzarella Sticks Special": "🧀", "Mozzarella Sticks Combo 2": "🧀", "Mozzarella Sticks Combo 3": "🧀", "Mozzarella Sticks Combo 4": "🧀",
      "Sweet Potato Fries Special": "🍠", "Sweet Potato Fries Combo 2": "🍠", "Sweet Potato Fries Combo 3": "🍠", "Sweet Potato Fries Combo 4": "🍠",
      "Mac & Cheese Special": "🧀", "Mac & Cheese Combo 2": "🧀", "Mac & Cheese Combo 3": "🧀", "Mac & Cheese Combo 4": "🧀",
      "Potato Wedges Special": "🥔", "Potato Wedges Combo 2": "🥔", "Potato Wedges Combo 3": "🥔", "Potato Wedges Combo 4": "🥔",

      // Drinks
      "Iced Cola Special": "🥤", "Iced Cola Combo 2": "🥤", "Iced Cola Combo 3": "🥤", "Iced Cola Combo 4": "🥤",
      "Fresh Lemonade Special": "🍋", "Fresh Lemonade Combo 2": "🍋", "Fresh Lemonade Combo 3": "🍋", "Fresh Lemonade Combo 4": "🍋",
      "Orange Juice Special": "🧃", "Orange Juice Combo 2": "🧃", "Orange Juice Combo 3": "🧃", "Orange Juice Combo 4": "🧃",
      "Vanilla Milkshake Special": "🥛", "Vanilla Milkshake Combo 2": "🥛", "Vanilla Milkshake Combo 3": "🥛", "Vanilla Milkshake Combo 4": "🥛",
      "Iced Peach Tea Special": "🧋", "Iced Peach Tea Combo 2": "🧋", "Iced Peach Tea Combo 3": "🧋", "Iced Peach Tea Combo 4": "🧋",
      "Sparkling Soda Special": "🫧", "Sparkling Soda Combo 2": "🫧", "Sparkling Soda Combo 3": "🫧", "Sparkling Soda Combo 4": "🫧",
      "Mango Smoothie Special": "🥭", "Mango Smoothie Combo 2": "🥭", "Mango Smoothie Combo 3": "🥭", "Mango Smoothie Combo 4": "🥭",
      "Special Chapman Special": "🍹", "Special Chapman Combo 2": "🍹", "Special Chapman Combo 3": "🍹", "Special Chapman Combo 4": "🍹",

      // Desserts
      "Lava Cake Special": "🍫", "Lava Cake Combo 2": "🍫", "Lava Cake Combo 3": "🍫", "Lava Cake Combo 4": "🍫",
      "NY Cheesecake Special": "🍰", "NY Cheesecake Combo 2": "🍰", "NY Cheesecake Combo 3": "🍰", "NY Cheesecake Combo 4": "🍰",
      "Apple Pie Special": "🥧", "Apple Pie Combo 2": "🥧", "Apple Pie Combo 3": "🥧", "Apple Pie Combo 4": "🥧",
      "Ice Cream Sundae Special": "🍨", "Ice Cream Sundae Combo 2": "🍨", "Ice Cream Sundae Combo 3": "🍨", "Ice Cream Sundae Combo 4": "🍨",
      "Fudge Brownie Special": "🥮", "Fudge Brownie Combo 2": "🥮", "Fudge Brownie Combo 3": "🥮", "Fudge Brownie Combo 4": "🥮",
      "Tiramisu Special": "🍮", "Tiramisu Combo 2": "🍮", "Tiramisu Combo 3": "🍮", "Tiramisu Combo 4": "🍮",
      "Belgian Waffle Special": "🧇", "Belgian Waffle Combo 2": "🧇", "Belgian Waffle Combo 3": "🧇", "Belgian Waffle Combo 4": "🧇",
      "Glazed Donut Special": "🍩", "Glazed Donut Combo 2": "🍩", "Glazed Donut Combo 3": "🍩", "Glazed Donut Combo 4": "🍩"
    };

    const itemPrefixes = {
      Pizza: ["Pepperoni", "Margherita", "BBQ Chicken", "Four Cheese", "Hawaiian", "Veggie Delight", "Meat Feast", "Truffle Mushroom"],
      Burgers: ["Classic Cheese", "Double Smash", "Bacon Deluxe", "Crispy Chicken", "Veggie Beyond", "Mushroom Swiss", "Spicy Zinger", "Avocado Beef"],
      Local: ["Smokey Jollof", "Suya Skewers", "Egusi Special", "Fried Rice", "Ofada Delicacy", "Spicy Asun", "Fisherman Soup", "Pepper Soup"],
      Sides: ["Crispy Fries", "Onion Rings", "Garlic Bread", "Coleslaw", "Mozzarella Sticks", "Sweet Potato Fries", "Mac & Cheese", "Potato Wedges"],
      Drinks: ["Iced Cola", "Fresh Lemonade", "Orange Juice", "Vanilla Milkshake", "Iced Peach Tea", "Sparkling Soda", "Mango Smoothie", "Special Chapman"],
      Desserts: ["Lava Cake", "NY Cheesecake", "Apple Pie", "Ice Cream Sundae", "Fudge Brownie", "Tiramisu", "Belgian Waffle", "Glazed Donut"]
    };

    const foodItems = [];

    for (let i = 1; i <= 100; i++) {
      const category = categories[(i - 1) % categories.length];
      const prefixes = itemPrefixes[category];
      const baseName = prefixes[(i - 1) % prefixes.length];
      const variantNum = Math.floor((i - 1) / (categories.length * prefixes.length)) + 1;
      const name = variantNum > 1 ? `${baseName} Combo ${variantNum}` : `${baseName} Special`;

      const assignedEmoji = preciseEmojiMap[name] || "🍽️";

      foodItems.push({
        id: i,
        name: name,
        category: category,
        price: ((i % 15) + 15) * 250,
        emoji: assignedEmoji,
        desc: `Freshly prepared ${name.toLowerCase()} made with premium ingredients.`
      });
    }

    // Check device local storage on page load so user stays logged in across sessions!
    window.addEventListener('DOMContentLoaded', () => {
      const savedEmail = localStorage.getItem('foodies_user_email');
      const savedUsername = localStorage.getItem('foodies_user_username');
      if (savedEmail && savedUsername) {
        openMenu(savedEmail, savedUsername);
      }
    });

    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.innerText = msg;
      toast.classList.remove('hidden');
      setTimeout(() => toast.classList.add('hidden'), 3000);
    }

    function showAuthScreen(id) {
      document.querySelectorAll('.auth-container .auth-card').forEach(el => el.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
    }

    function openMenu(email, username) {
      activeUserEmail = email;
      // Save credentials in browser localStorage so it saves for future purpose on user's phone!
      localStorage.setItem('foodies_user_email', email);
      localStorage.setItem('foodies_user_username', username);

      document.getElementById('auth-wrapper').classList.add('hidden');
      document.getElementById('menu-wrapper').classList.remove('hidden');
      document.getElementById('user-display-email').innerText = username;
      renderFoods(foodItems);
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
        showToast("Account created successfully!");
        openMenu(data.email, data.username);
      } else {
        showToast(data.error);
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
        showToast(data.error || "Invalid email or password");
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
        openMenu(data.email, data.username);
      } else {
        showToast(data.error);
      }
    }

    async function handleLogout() {
      await fetch('/api/logout', { method: 'POST' });
      // Clear persistent browser credentials on explicit logout
      localStorage.removeItem('foodies_user_email');
      localStorage.removeItem('foodies_user_username');
      cart = [];
      document.getElementById('nav-rider-btn').classList.add('hidden');
      document.getElementById('menu-wrapper').classList.add('hidden');
      document.getElementById('auth-wrapper').classList.remove('hidden');
      showAuthScreen('login-screen');
    }

    function renderFoods(items) {
      const grid = document.getElementById('foodGrid');
      grid.innerHTML = '';

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
            <button class="btn-add" onclick="addToCart(${item.id})">+ Add</button>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function filterCategory(category) {
      document.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
      event.target.classList.add('active');
      if (category === 'All') {
        renderFoods(foodItems);
      } else {
        renderFoods(foodItems.filter(i => i.category === category));
      }
    }

    function filterFoods() {
      const query = document.getElementById('searchInput').value.toLowerCase();
      renderFoods(foodItems.filter(i => i.name.toLowerCase().includes(query) || i.desc.toLowerCase().includes(query)));
    }

    function addToCart(id) {
      const item = foodItems.find(f => f.id === id);
      cart.push({ ...item, cartInstanceId: Date.now() + Math.random() });
      updateCartUI();
      showToast(`${item.name} added to cart`);
    }

    function removeFromCart(cartInstanceId) {
      const index = cart.findIndex(item => item.cartInstanceId === cartInstanceId);
      if (index !== -1) {
        const removedItem = cart[index];
        cart.splice(index, 1);
        openCartModal();
        updateCartUI();
        showToast(`Removed ${removedItem.name} from cart`);
      }
    }

    function updateCartUI() {
      document.getElementById('cart-count-badge').innerText = cart.length;
    }

    function openCartModal() {
      const container = document.getElementById('cart-items-container');
      container.innerHTML = '';
      let total = 0;

      if (cart.length === 0) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; font-size: 13px; padding: 20px;">Your cart is empty</div>`;
      } else {
        cart.forEach((item) => {
          total += item.price;
          container.innerHTML += `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #f1f5f9; font-size: 13px;">
              <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
                <span style="font-size: 20px;">${item.emoji}</span>
                <span style="font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 150px;">${item.name}</span>
              </div>
              <div style="display: flex; align-items: center; gap: 12px;">
                <b style="color: var(--primary);">₦${item.price.toLocaleString()}</b>
                <button onclick="removeFromCart(${item.cartInstanceId})" style="background: #fee2e2; color: #ef4444; border: none; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer;">Remove</button>
              </div>
            </div>
          `;
        });
      }

      document.getElementById('cart-total-price').innerText = `₦${total.toLocaleString()}`;
      document.getElementById('cartModal').classList.remove('hidden');
    }

    function closeCartModal() { document.getElementById('cartModal').classList.add('hidden'); }

    function payWithPaystack() {
      const address = document.getElementById('delivery-address').value;
      if (!address) return showToast("Please enter a delivery address!");
      if (cart.length === 0) return showToast("Cart is empty!");

      const totalAmount = cart.reduce((sum, item) => sum + item.price, 0);

      const handler = PaystackPop.setup({
        key: PAYSTACK_PUBLIC_KEY,
        email: activeUserEmail,
        amount: totalAmount * 100,
        currency: "NGN",
        callback: function(response) {
          closeCartModal();
          cart = [];
          updateCartUI();
          showToast("Payment Successful!");
          document.getElementById('nav-rider-btn').classList.remove('hidden');
          toggleRiderChatModal();
        },
        onClose: function() { showToast("Payment cancelled."); }
      });
      handler.openIframe();
    }

    function toggleRiderChatModal() {
      const modal = document.getElementById('chatModal');
      modal.classList.toggle('hidden');
      if (!modal.classList.contains('hidden')) {
        initRiderMap();
      }
    }

    function closeChatModal() { document.getElementById('chatModal').classList.add('hidden'); }

    function initRiderMap() {
      if (!map) {
        map = L.map('rider-map').setView([9.05785, 7.49508], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);

        const riderIcon = L.divIcon({
          html: '<span style="font-size:24px;">🛵</span>',
          className: 'rider-leaflet-icon',
          iconSize: [30, 30]
        });

        riderMarker = L.marker([9.05785, 7.49508], { icon: riderIcon }).addTo(map);
      }

      setTimeout(() => map.invalidateSize(), 300);

      if (pollRiderInterval) clearInterval(pollRiderInterval);
      pollRiderInterval = setInterval(async () => {
        const res = await fetch('/api/rider-location');
        const data = await res.json();
        const pos = [data.lat, data.lng];
        riderMarker.setLatLng(pos);
        map.panTo(pos);
        document.getElementById('live-rider-status').innerText = `● ${data.status}`;
      }, 1000);
    }

    function startVisitorMonitoring() {
      setInterval(async () => {
        const res = await fetch('/api/active-visitors');
        const data = await res.json();
        document.getElementById('live-visitors').innerText = `● Online: ${data.online_users}`;
      }, 3000);
    }

    function sendChatMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;

      const body = document.getElementById('chatMessages');
      body.innerHTML += `<div class="chat-msg user">${text}</div>`;
      input.value = '';
      body.scrollTop = body.scrollHeight;

      setTimeout(() => {
        body.innerHTML += `<div class="chat-msg rider">Rider received: "${text}". I'm on my way!</div>`;
        body.scrollTop = body.scrollHeight;
      }, 1000);
    }

    function handleChatKeyPress(e) { if (e.key === 'Enter') sendChatMessage(); }
  </script>
</body>
</html>
"""


# ---------------------------------------------------------
# FLASK BACKEND ROUTES
# ---------------------------------------------------------
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    email, password, pin = data.get('email'), data.get('password'), data.get('pin')

    if not email or not password or not pin:
        return jsonify({"success": False, "error": "All fields are required"}), 400

    save_user(email, password, pin)
    session['user'] = email
    return jsonify({"success": True, "email": email, "username": extract_username(email)})


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email, password = data.get('email'), data.get('password')
    user = get_user(email)

    if user and user['password'] == password:
        session['temp_user'] = email
        return jsonify({"require_pin": True})
    return jsonify({"require_pin": False, "error": "Invalid email or password"}), 401


@app.route('/api/verify-pin', methods=['POST'])
def verify_pin():
    pin = request.get_json().get('pin')
    email = session.get('temp_user')
    user = get_user(email) if email else None

    if user and user['pin'] == pin:
        session['user'] = email
        session.pop('temp_user', None)
        return jsonify({"success": True, "email": email, "username": extract_username(email)})
    return jsonify({"success": False, "error": "Incorrect PIN"}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route('/api/rider-location', methods=['GET'])
def get_rider_location():
    if rider_status['lat'] < rider_status['dest_lat']:
        rider_status['lat'] += 0.0003
        rider_status['lng'] -= 0.0015
    else:
        rider_status['status'] = "Arrived at location!"
    return jsonify(rider_status)


@app.route('/api/active-visitors', methods=['GET'])
def get_active_visitors():
    now = time.time()
    online = [ip for ip, last_seen in active_visitors.items() if now - last_seen < 120]
    return jsonify({"online_users": len(online)})


# ---------------------------------------------------------
# SERVER RUNNER
# ---------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
