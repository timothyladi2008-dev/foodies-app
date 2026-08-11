import sqlite3
from flask import Flask, jsonify, request, session, render_template_string

app = Flask(__name__)
app.secret_key = "super_secret_foodies_key"
DB_FILE = "users.db"


# ---------------------------------------------------------
# DATABASE SETUP (Creates users.db with Static PIN support)
# ---------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS users
                       (
                           email
                           TEXT
                           PRIMARY
                           KEY,
                           password
                           TEXT
                           NOT
                           NULL,
                           pin
                           TEXT
                           NOT
                           NULL
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
            return {
                "email": row[0],
                "password": row[1],
                "pin": row[2]
            }
    return None


def save_user(email, password, pin):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
                       INSERT INTO users (email, password, pin)
                       VALUES (?, ?, ?)
                       ''', (email, password, pin))
        conn.commit()


# ---------------------------------------------------------
# FRONTEND TEMPLATE
# ---------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Foodies - Express Delivery</title>
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://js.paystack.co/v1/inline.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Poppins', sans-serif; }
    body { min-height: 100vh; background: linear-gradient(135deg, #74ebd5 0%, #9aceff 100%); background-attachment: fixed; color: #1e293b; position: relative; }
    body::before { content: ""; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80"><text x="10" y="25" fill="rgba(255,255,255,0.22)" font-size="12" font-weight="bold">foodies</text></svg>'); background-repeat: repeat; pointer-events: none; z-index: 0; }
    .hidden { display: none !important; }
    .auth-container { min-height: 100vh; display: flex; justify-content: center; align-items: center; position: relative; z-index: 1; }
    .card { background: rgba(255, 255, 255, 0.35); backdrop-filter: blur(16px); padding: 35px 30px; border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.4); box-shadow: 0 10px 30px rgba(0,0,0,0.1); width: 100%; max-width: 400px; text-align: center; }
    h2 { font-size: 26px; margin-bottom: 8px; color: #0f172a; }
    p { font-size: 13px; color: #475569; margin-bottom: 20px; }
    input { width: 100%; padding: 12px; margin-bottom: 15px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.6); background: rgba(255,255,255,0.6); outline: none; }
    button { width: 100%; padding: 12px; font-weight: 600; background: linear-gradient(135deg, #ff6b6b, #ff8e53); color: white; border: none; border-radius: 10px; cursor: pointer; margin-top: 5px; }
    .toggle-link { margin-top: 15px; font-size: 12px; color: #0f172a; cursor: pointer; text-decoration: underline; font-weight: 600; }
    .app-container { max-width: 1200px; margin: 0 auto; padding: 20px; position: relative; z-index: 1; }
    nav { display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; background: rgba(255, 255, 255, 0.35); backdrop-filter: blur(12px); border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.4); margin-bottom: 30px; }
    .logo { font-size: 24px; font-weight: 700; color: #0f172a; }
    .nav-actions { display: flex; gap: 15px; align-items: center; }
    .btn-logout { background: #0f172a; color: white; padding: 8px 16px; border-radius: 10px; font-size: 13px; cursor: pointer; border: none; }
    .hero { text-align: center; margin: 10px 0 30px; }
    .search-bar { max-width: 550px; margin: 15px auto 0; display: flex; background: rgba(255, 255, 255, 0.7); backdrop-filter: blur(10px); border-radius: 50px; padding: 6px 10px 6px 20px; border: 1px solid rgba(255, 255, 255, 0.8); }
    .search-bar input { border: none; background: transparent; width: 100%; outline: none; margin: 0; }
    .categories { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 10px; margin-bottom: 25px; }
    .category-chip { background: rgba(255, 255, 255, 0.4); padding: 8px 18px; border-radius: 30px; border: 1px solid rgba(255, 255, 255, 0.5); cursor: pointer; white-space: nowrap; font-weight: 500; }
    .category-chip.active { background: #0f172a; color: white; }
    .food-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }
    .food-card { background: rgba(255, 255, 255, 0.45); backdrop-filter: blur(12px); border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.5); padding: 16px; }
    .food-img { width: 100%; height: 110px; background: rgba(255, 255, 255, 0.5); border-radius: 14px; display: flex; align-items: center; justify-content: center; font-size: 42px; margin-bottom: 10px; }
    .food-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .food-desc { font-size: 12px; color: #475569; height: 34px; overflow: hidden; }
    .food-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }
    .price { font-size: 15px; font-weight: 700; color: #e84118; }
    .btn-buy { background: #0f172a; color: white; border: none; padding: 6px 14px; border-radius: 8px; font-weight: 600; cursor: pointer; }
    .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15, 23, 42, 0.5); backdrop-filter: blur(8px); display: flex; justify-content: center; align-items: center; z-index: 10; }
    .checkout-card { background: white; border-radius: 20px; padding: 30px; width: 90%; max-width: 420px; text-align: left; }
    .close-btn { float: right; cursor: pointer; font-weight: bold; }
  </style>
</head>
<body>

  <div id="auth-wrapper" class="auth-container">
    <div id="login-screen" class="card">
      <h2>Foodies 🍕</h2>
      <p>Sign in to your account</p>
      <input type="email" id="login-email" placeholder="you@example.com">
      <input type="password" id="login-password" placeholder="••••••••">
      <button onclick="handleLogin()">Sign In</button>
      <div class="toggle-link" onclick="showAuthScreen('signup-screen')">Don't have an account? Create one</div>
    </div>

    <div id="signup-screen" class="card hidden">
      <h2>Join Foodies 🍕</h2>
      <p>Create account & set your permanent 6-digit PIN</p>
      <input type="email" id="signup-email" placeholder="you@example.com">
      <input type="password" id="signup-password" placeholder="Password">
      <input type="text" id="signup-pin" maxlength="6" placeholder="Create 6-Digit PIN (e.g. 282828)" style="text-align: center; letter-spacing: 2px;">
      <button onclick="handleSignup()">Create Account</button>
      <div class="toggle-link" onclick="showAuthScreen('login-screen')">Already have an account? Sign In</div>
    </div>

    <div id="verify-screen" class="card hidden">
      <h2>Enter Security PIN 🛡️</h2>
      <p>Enter your 6-digit PIN to access your account:</p>
      <input type="text" id="verify-pin" maxlength="6" placeholder="282828" style="text-align: center; font-size: 20px; letter-spacing: 4px;">
      <button onclick="handleVerifyPIN()">Verify & Login</button>
    </div>
  </div>

  <div id="menu-wrapper" class="app-container hidden">
    <nav>
      <div class="logo">Foodies 🍕</div>
      <div class="nav-actions">
        <span id="user-display-email" style="font-weight: 500;"></span>
        <button class="btn-logout" onclick="handleLogout()">Log Out</button>
      </div>
    </nav>

    <div class="hero">
      <h1>Explore Our Full Menu</h1>
      <div class="search-bar">
        <input type="text" id="searchInput" placeholder="Search 50+ meals, drinks, desserts..." onkeyup="filterFoods()">
      </div>
    </div>

    <div class="categories" id="categoryContainer">
      <div class="category-chip active" onclick="filterCategory('All')">🔥 All</div>
      <div class="category-chip" onclick="filterCategory('Pizza')">🍕 Pizza</div>
      <div class="category-chip" onclick="filterCategory('Burgers')">🍔 Burgers</div>
      <div class="category-chip" onclick="filterCategory('Local')">🍲 Local Specials</div>
      <div class="category-chip" onclick="filterCategory('Sides')">🍟 Sides</div>
      <div class="category-chip" onclick="filterCategory('Drinks')">🧃 Drinks</div>
      <div class="category-chip" onclick="filterCategory('Desserts')">🍰 Desserts</div>
    </div>

    <div class="food-grid" id="foodGrid"></div>
  </div>

  <div id="checkoutModal" class="modal-overlay hidden">
    <div class="checkout-card">
      <span class="close-btn" onclick="closeCheckout()">✕</span>
      <h3 style="margin-bottom: 10px;">Checkout & Pay</h3>
      <p id="checkout-item-name" style="font-weight: 600; color: #0f172a; margin-bottom: 5px;"></p>
      <p id="checkout-item-price" style="color: #e84118; font-weight: 700; margin-bottom: 15px;"></p>

      <label style="font-size: 12px; font-weight: 600;">Delivery Address</label>
      <input type="text" id="delivery-address" placeholder="e.g. Sauka new site gate, House 5">

      <button onclick="payWithPaystack()">Pay Now</button>
    </div>
  </div>

  <script>
    let activeUserEmail = "";
    let selectedItem = null;
    const PAYSTACK_PUBLIC_KEY = "pk_test_15c3892f5824f99266724433804c708899e1994f";

    function showAuthScreen(id) {
      document.querySelectorAll('.auth-container .card').forEach(el => el.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
    }

    function openMenu(email) {
      activeUserEmail = email;
      document.getElementById('auth-wrapper').classList.add('hidden');
      document.getElementById('menu-wrapper').classList.remove('hidden');
      document.getElementById('user-display-email').innerText = "Hello, " + email;
      renderFoods(foodItems);
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
        alert("Account created with PIN " + pin + "! Logging you in...");
        openMenu(data.email);
      } else {
        alert(data.error);
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
        alert(data.error);
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
        openMenu(data.email);
      } else {
        alert(data.error);
      }
    }

    async function handleLogout() {
      await fetch('/api/logout', { method: 'POST' });
      document.getElementById('menu-wrapper').classList.add('hidden');
      document.getElementById('auth-wrapper').classList.remove('hidden');
      showAuthScreen('login-screen');
    }

    const foodItems = [
      { id: 1, name: "Pepperoni Passion", category: "Pizza", price: 12500, icon: "🍕", desc: "Classic tomato sauce and double pepperoni" },
      { id: 2, name: "Margherita Supreme", category: "Pizza", price: 9800, icon: "🍕", desc: "Fresh basil, mozzarella, and plum tomatoes" },
      { id: 3, name: "BBQ Chicken Pizza", category: "Pizza", price: 13500, icon: "🍕", desc: "Grilled chicken with smoky BBQ sauce" },
      { id: 4, name: "Hawaiian Twist", category: "Pizza", price: 11000, icon: "🍕", desc: "Juicy pineapple chunks and smoked ham" },
      { id: 5, name: "Veggie Garden", category: "Pizza", price: 10500, icon: "🍕", desc: "Bell peppers, onions, mushrooms & olives" },
      { id: 6, name: "Meat Lovers Deluxe", category: "Pizza", price: 14500, icon: "🍕", desc: "Sausage, bacon, pepperoni, and beef" },
      { id: 7, name: "Classic Cheeseburger", category: "Burgers", price: 6500, icon: "🍔", desc: "Beef patty with cheddar and lettuce" },
      { id: 8, name: "Double Smash Burger", category: "Burgers", price: 8500, icon: "🍔", desc: "Two seared beef patties with special sauce" },
      { id: 9, name: "Crispy Chicken Burger", category: "Burgers", price: 7000, icon: "🍔", desc: "Fried chicken breast with mayo & pickles" },
      { id: 10, name: "Spicy Jalapeño Burger", category: "Burgers", price: 7500, icon: "🍔", desc: "Pepper jack cheese and sliced jalapeños" },
      { id: 11, name: "Mushroom Swiss Burger", category: "Burgers", price: 8000, icon: "🍔", desc: "Sautéed mushrooms and melted Swiss" },
      { id: 12, name: "Veggie Bean Burger", category: "Burgers", price: 6000, icon: "🍔", desc: "Black bean patty with avocado spread" },
      { id: 13, name: "Smokey Jollof Rice", category: "Local", price: 4500, icon: "🍲", desc: "Served with fried plantains and chicken" },
      { id: 14, name: "Suya Beef Skewers", category: "Local", price: 3500, icon: "🍢", desc: "Spicy grilled beef with sliced onions" },
      { id: 15, name: "Fried Rice & Chicken", category: "Local", price: 4800, icon: "🍛", desc: "Seasoned rice with mixed vegetables" },
      { id: 16, name: "Pounded Yam & Egusi", category: "Local", price: 5500, icon: "🍲", desc: "Rich melon seed soup with tender beef" },
      { id: 17, name: "Peppered Goat Meat", category: "Local", price: 6500, icon: "🥩", desc: "Spicy tossed goat meat chunks" },
      { id: 18, name: "Catfish Pepper Soup", category: "Local", price: 7000, icon: "🥣", desc: "Hot traditional herbal fish soup" },
      { id: 19, name: "Ewa Agoyin & Bread", category: "Local", price: 3000, icon: "🫘", desc: "Mashed beans with dark spicy palm oil sauce" },
      { id: 20, name: "Ofada Rice & Stew", category: "Local", price: 5000, icon: "🍱", desc: "Unpolished rice with green pepper sauce" },
      { id: 21, name: "Crispy French Fries", category: "Sides", price: 2500, icon: "🍟", desc: "Golden salted potato fries" },
      { id: 22, name: "Loaded Cheese Fries", category: "Sides", price: 3800, icon: "🍟", desc: "Fries topped with melted cheddar and bacon" },
      { id: 23, name: "Onion Rings", category: "Sides", price: 3000, icon: "🧅", desc: "Battered and deep-fried onion rings" },
      { id: 24, name: "Garlic Breadsticks", category: "Sides", price: 2800, icon: "🥖", desc: "Warm bread with garlic butter" },
      { id: 25, name: "Buffalo Wings (6pcs)", category: "Sides", price: 5500, icon: "🍗", desc: "Tossed in hot tangy buffalo sauce" },
      { id: 26, name: "Fried Plantain Dodo", category: "Sides", price: 2000, icon: "🍌", desc: "Sweet fried ripe plantain slices" },
      { id: 27, name: "Mozzarella Sticks", category: "Sides", price: 4200, icon: "🧀", desc: "Fried cheese sticks with marinara dip" },
      { id: 28, name: "Coleslaw Salad", category: "Sides", price: 1800, icon: "🥗", desc: "Fresh shredded cabbage and carrots" },
      { id: 29, name: "Mac & Cheese Cup", category: "Sides", price: 3200, icon: "🧀", desc: "Creamy baked macaroni and cheese" },
      { id: 30, name: "Iced Cola", category: "Drinks", price: 1000, icon: "🥤", desc: "Chilled 500ml sparkling soda" },
      { id: 31, name: "Fresh Orange Juice", category: "Drinks", price: 2500, icon: "🍊", desc: "100% natural cold-pressed oranges" },
      { id: 32, name: "Strawberry Milkshake", category: "Drinks", price: 3500, icon: "🥤", desc: "Blended ice cream and fresh berries" },
      { id: 33, name: "Mango Passion Smoothie", category: "Drinks", price: 3800, icon: "🧃", desc: "Tropical fruit blend with yogurt" },
      { id: 34, name: "Iced Lemon Tea", category: "Drinks", price: 2000, icon: "🍹", desc: "Refreshing brewed tea with lemon" },
      { id: 35, name: "Sparkling Water", category: "Drinks", price: 1200, icon: "💧", desc: "Zero calorie mineral water" },
      { id: 36, name: "Chocolate Boba Tea", category: "Drinks", price: 4000, icon: "🧋", desc: "Milk tea with chewy tapioca pearls" },
      { id: 37, name: "Zobo Drink", category: "Drinks", price: 1500, icon: "🍷", desc: "Chilled hibiscus juice infused with ginger" },
      { id: 38, name: "Chocolate Lava Cake", category: "Desserts", price: 4500, icon: "🧁", desc: "Warm cake with a molten chocolate center" },
      { id: 39, name: "New York Cheesecake", category: "Desserts", price: 5000, icon: "🍰", desc: "Classic dense and creamy cheesecake" },
      { id: 40, name: "Glazed Donuts (3pcs)", category: "Desserts", price: 3000, icon: "🍩", desc: "Soft sugary glazed ring donuts" },
      { id: 41, name: "Vanilla Ice Cream Bowl", category: "Desserts", price: 2500, icon: "🍨", desc: "Two scoops topped with chocolate syrup" },
      { id: 42, name: "Red Velvet Cupcake", category: "Desserts", price: 2200, icon: "🧁", desc: "Topped with cream cheese frosting" },
      { id: 43, name: "Apple Pie Slice", category: "Desserts", price: 3800, icon: "🥧", desc: "Warm cinnamon apple pie slice" },
      { id: 44, name: "Choco Chip Cookies (4pcs)", category: "Desserts", price: 2000, icon: "🍪", desc: "Freshly baked crunchy cookies" },
      { id: 45, name: "Crispy Spring Rolls (4pcs)", category: "Sides", price: 2800, icon: "🥟", desc: "Fried vegetable spring rolls" },
      { id: 46, name: "Meat Pie", category: "Sides", price: 1500, icon: "🥧", desc: "Flaky pastry stuffed with minced meat" },
      { id: 47, name: "Samosa Box (5pcs)", category: "Sides", price: 3200, icon: "📐", desc: "Crispy triangular minced beef pastries" },
      { id: 48, name: "Shawarma Wrap", category: "Burgers", price: 4000, icon: "🌯", desc: "Chicken shawarma with sausage and garlic sauce" },
      { id: 49, name: "Grilled Club Sandwich", category: "Burgers", price: 4500, icon: "🥪", desc: "Triple decker bread with egg and bacon" },
      { id: 50, name: "Churro Sticks with Fudge", category: "Desserts", price: 3500, icon: "🥖", desc: "Cinnamon sugar sticks with chocolate dip" }
    ];

    let activeCategory = 'All';

    function renderFoods(items) {
      const grid = document.getElementById('foodGrid');
      grid.innerHTML = '';
      items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'food-card';
        card.innerHTML = `
          <div class="food-img">${item.icon}</div>
          <div class="food-title">${item.name}</div>
          <div class="food-desc">${item.desc}</div>
          <div class="food-meta">
            <span class="price">₦${item.price.toLocaleString()}</span>
            <button class="btn-buy" onclick="openCheckout('${item.name}', ${item.price})">Buy</button>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function filterCategory(cat) {
      activeCategory = cat;
      document.querySelectorAll('.category-chip').forEach(chip => {
        chip.classList.toggle('active', chip.innerText.includes(cat));
      });
      filterFoods();
    }

    function filterFoods() {
      const query = document.getElementById('searchInput').value.toLowerCase();
      const filtered = foodItems.filter(item => {
        const matchesCat = activeCategory === 'All' || item.category === activeCategory;
        const matchesQuery = item.name.toLowerCase().includes(query) || item.desc.toLowerCase().includes(query);
        return matchesCat && matchesQuery;
      });
      renderFoods(filtered);
    }

    function openCheckout(name, price) {
      selectedItem = { name, price };
      document.getElementById('checkout-item-name').innerText = name;
      document.getElementById('checkout-item-price').innerText = "₦" + price.toLocaleString();
      document.getElementById('checkoutModal').classList.remove('hidden');
    }

    function closeCheckout() {
      document.getElementById('checkoutModal').classList.add('hidden');
    }

    function payWithPaystack() {
      const address = document.getElementById('delivery-address').value;
      if (!address) {
        alert("Please enter a delivery address.");
        return;
      }

      const handler = PaystackPop.setup({
        key: PAYSTACK_PUBLIC_KEY,
        email: activeUserEmail,
        amount: selectedItem.price * 100,
        currency: "NGN",
        ref: 'FOODIES_' + Math.floor((Math.random() * 1000000000) + 1),
        callback: function(response) {
          alert('Payment successful! Transaction Ref: ' + response.reference);
          closeCheckout();
        },
        onClose: function() {
          alert('Transaction cancelled.');
        }
      });

      handler.openIframe();
    }
  </script>
</body>
</html>
"""


@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)


# ---------------------------------------------------------
# API ROUTES
# ---------------------------------------------------------
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    pin = data.get('pin')

    if not email or not password or not pin:
        return jsonify({"error": "Please fill in email, password, and 6-digit PIN"}), 400

    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"error": "PIN must be exactly 6 digits"}), 400

    if get_user(email):
        return jsonify({"error": "An account with this email already exists"}), 400

    save_user(email, password, pin)
    session['user'] = email

    return jsonify({"success": True, "email": email})


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    user = get_user(email)
    if not user or user["password"] != password:
        return jsonify({"error": "Invalid email or password"}), 401

    session['pending_user'] = email
    return jsonify({"require_pin": True})


@app.route('/api/verify-pin', methods=['POST'])
def verify_pin():
    data = request.get_json()
    entered_pin = data.get('pin')
    email = session.get('pending_user')

    user = get_user(email) if email else None
    if not user:
        return jsonify({"error": "Session expired"}), 400

    if user["pin"] == entered_pin:
        session['user'] = session.pop('pending_user', None)
        return jsonify({"success": True, "email": email})

    return jsonify({"error": "Incorrect PIN. Try again."}), 400


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)