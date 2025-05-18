from flask import Flask, render_template, redirect, request, session, url_for, flash, abort
import mysql.connector
import os
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'static/images'

# MySQL Configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Maadcity23!',
    'database': 'food_app_db'
}


def get_db_connection():
    return mysql.connector.connect(**db_config)


# -------------------- Helper Functions --------------------
def check_user_exists(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user WHERE username = %s", (email,))
    exists = cursor.fetchone()
    cursor.close()
    conn.close()
    return exists is not None


def calculate_cart_total(cart_id):
    conn = get_db_connection()
    cursor = conn.cursor(buffered=True)  # Buffered cursor

    try:
        cursor.execute("""
                       SELECT SUM(mi.price * ci.quantity)
                       FROM cart_item ci
                                JOIN menu_item mi ON ci.menu_item_id = mi.menu_item_id
                       WHERE ci.cart_id = %s
                       """, (cart_id,))
        total = cursor.fetchone()[0] or 0.0
    finally:
        cursor.close()
        conn.close()

    return total


# -------------------- Authentication Routes --------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        firstname = request.form['firstname']
        lastname = request.form['lastname']
        username = request.form['username']
        password = request.form['password']
        user_type = request.form['user_type']

        if check_user_exists(username):
            flash('Username already exists!', 'danger')
            return redirect(url_for('register'))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
                       INSERT INTO user (firstname, lastname, username, password, user_type)
                       VALUES (%s, %s, %s, %s, %s)
                       """, (firstname, lastname, username, password, user_type))
        conn.commit()
        cursor.close()
        conn.close()

        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
                       SELECT *
                       FROM user
                       WHERE username = %s
                         AND password = %s
                       """, (username, password))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            session['user_id'] = user['user_id']
            session['user_type'] = user['user_type']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials!', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# -------------------- Customer Routes --------------------
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_type = session.get('user_type')
    if user_type == 'Customer':
        return redirect(url_for('customer_dashboard'))
    else:
        return redirect(url_for('manager_dashboard'))


@app.route('/customer/dashboard')
def customer_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Kullanıcının tüm şehirlerini al
        cursor.execute("SELECT DISTINCT city FROM address WHERE user_id = %s", (user_id,))
        cities = [row['city'] for row in cursor.fetchall()]

        if not cities:
            flash('Lütfen adres ekleyin!', 'warning')
            return redirect(url_for('settings'))

        # Restoranları filtrele
        query = """
            SELECT r.*, AVG(rt.rating) as avg_rating
            FROM restaurant r
            LEFT JOIN rating rt ON r.restaurant_id = rt.restaurant_id
            WHERE r.city IN ({})
            GROUP BY r.restaurant_id
        """.format(','.join(['%s'] * len(cities)))

        cursor.execute(query, tuple(cities))
        restaurants = cursor.fetchall()

    except mysql.connector.Error as err:
        flash(f'Hata: {err}', 'danger')
        restaurants = []
    finally:
        cursor.close()
        conn.close()

    return render_template('customer_dashboard.html', restaurants=restaurants)


@app.route('/restaurant/<int:restaurant_id>')
def view_restaurant(restaurant_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get restaurant details
    cursor.execute("SELECT * FROM restaurant WHERE restaurant_id = %s", (restaurant_id,))
    restaurant = cursor.fetchone()

    # Get menu items
    cursor.execute("SELECT * FROM menu_item WHERE restaurant_id = %s", (restaurant_id,))
    menu_items = cursor.fetchall()

    # Get ratings
    cursor.execute("""
                   SELECT r.*, u.username
                   FROM rating r
                            JOIN user u ON r.customer_id = u.user_id
                   WHERE restaurant_id = %s
                   """, (restaurant_id,))
    ratings = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('restaurant.html', restaurant=restaurant, menu_items=menu_items, ratings=ratings)


@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    menu_item_id = request.form['menu_item_id']
    quantity = request.form['quantity']

    conn = get_db_connection()
    cursor = conn.cursor(buffered=True)  # Buffered cursor

    try:
        # Aktif sepeti kontrol et
        cursor.execute("""
                       SELECT cart_id
                       FROM cart
                       WHERE customer_id = %s
                         AND status = 'Preparing'
                       """, (session['user_id'],))
        cart = cursor.fetchone()  # Sonucu oku

        if not cart:
            cursor.execute("""
                           INSERT INTO cart (customer_id, restaurant_id, status)
                           VALUES (%s, (SELECT restaurant_id FROM menu_item WHERE menu_item_id = %s), 'Preparing')
                           """, (session['user_id'], menu_item_id))
            cart_id = cursor.lastrowid
        else:
            cart_id = cart[0]

        # Ürünü sepete ekle
        cursor.execute("""
                       INSERT INTO cart_item (cart_id, menu_item_id, quantity)
                       VALUES (%s, %s, %s) ON DUPLICATE KEY
                       UPDATE quantity = quantity +
                       VALUES (quantity)
                       """, (cart_id, menu_item_id, quantity))

        # Sepet toplamını güncelle
        total = calculate_cart_total(cart_id)
        cursor.execute("UPDATE cart SET total = %s WHERE cart_id = %s", (total, cart_id))

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('view_cart'))


@app.route('/cart')
def view_cart():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Sepet öğelerini ve toplamı çek
        cursor.execute("""
                       SELECT ci.cart_item_id,
                              mi.name,
                              mi.price,
                              ci.quantity,
                              (mi.price * ci.quantity) AS item_total # Her ürünün toplamını hesapla
                       FROM cart c
                                JOIN cart_item ci ON c.cart_id = ci.cart_id
                                JOIN menu_item mi ON ci.menu_item_id = mi.menu_item_id
                       WHERE c.customer_id = %s
                         AND c.status = 'Preparing'
                       """, (user_id,))

        cart_items = cursor.fetchall()

        # Grand Total'i backend'de hesapla
        grand_total = sum(item['item_total'] for item in cart_items)

    except mysql.connector.Error as err:
        flash(f'Hata: {err}', 'danger')
        cart_items = []
        grand_total = 0
    finally:
        cursor.close()
        conn.close()

    return render_template('cart.html', cart_items=cart_items, grand_total=grand_total)




@app.route('/checkout')
def checkout():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Find the active cart
        cursor.execute("""
            SELECT cart_id FROM cart
            WHERE customer_id = %s AND status = 'Preparing'
            LIMIT 1
        """, (user_id,))
        result = cursor.fetchone()

        if not result:
            flash("No active cart to checkout.", "warning")
            return redirect(url_for('view_cart'))

        cart_id = result[0]

        # Infer restaurant_id from items in cart_item (JOIN with menu_item)
        cursor.execute("""
            SELECT mi.restaurant_id
            FROM cart_item ci
            JOIN menu_item mi ON ci.menu_item_id = mi.menu_item_id
            WHERE ci.cart_id = %s
            LIMIT 1
        """, (cart_id,))
        res = cursor.fetchone()

        if not res:
            flash("No menu items found in cart to determine restaurant.", "warning")
            return redirect(url_for('view_cart'))

        restaurant_id = res[0]

        # Calculate total (optional but important)
        cursor.execute("""
            SELECT SUM(mi.price * ci.quantity)
            FROM cart_item ci
            JOIN menu_item mi ON ci.menu_item_id = mi.menu_item_id
            WHERE ci.cart_id = %s
        """, (cart_id,))
        total = cursor.fetchone()[0] or 0.0

        # Update cart with restaurant_id, status and total
        cursor.execute("""
            UPDATE cart
            SET status = 'Pending', restaurant_id = %s, total = %s
            WHERE cart_id = %s
        """, (restaurant_id, total, cart_id))
        conn.commit()

        # Create new empty cart
        cursor.execute("""
            INSERT INTO cart (customer_id, restaurant_id, total, status)
            VALUES (%s, %s, %s, 'Preparing')
        """, (user_id, restaurant_id, 0.0))
        conn.commit()

    except mysql.connector.Error as err:
        flash(f"Error while checking out: {err}", "danger")
        return redirect(url_for('view_cart'))

    finally:
        cursor.close()
        conn.close()

    return render_template("checkout.html")




# -------------------- Restaurant Manager Routes --------------------
@app.route('/manager/dashboard')
def manager_dashboard():
    if 'user_id' not in session or session.get('user_type') != 'Manager':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get managed restaurants
    cursor.execute("SELECT * FROM restaurant WHERE manager_id = %s", (session['user_id'],))
    restaurants = cursor.fetchall()

    # Get recent orders
    cursor.execute("""
                   SELECT c.*, u.username
                   FROM cart c
                            JOIN user u ON c.customer_id = u.user_id
                   WHERE c.restaurant_id IN (SELECT restaurant_id
                                             FROM restaurant
                                             WHERE manager_id = %s)
                   ORDER BY timestamp DESC LIMIT 5
                   """, (session['user_id'],))

    recent_orders = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('manager_dashboard.html', restaurants=restaurants, recent_orders=recent_orders)


@app.route('/manager/restaurant/<int:restaurant_id>')
def manage_restaurant(restaurant_id):
    if 'user_id' not in session or session.get('user_type') != 'Manager':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Verify ownership
    cursor.execute("SELECT * FROM restaurant WHERE restaurant_id = %s AND manager_id = %s",
                   (restaurant_id, session['user_id']))
    restaurant = cursor.fetchone()

    if not restaurant:
        abort(403)

    # Get menu items
    cursor.execute("SELECT * FROM menu_item WHERE restaurant_id = %s", (restaurant_id,))
    menu_items = cursor.fetchall()

    # Get recent orders for this restaurant via menu_item join
    cursor.execute("""
                   SELECT DISTINCT c.cart_id, u.username, SUM(ci.quantity * mi.price) AS total, c.status
                   FROM cart c
                            JOIN user u ON c.customer_id = u.user_id
                            JOIN cart_item ci ON c.cart_id = ci.cart_id
                            JOIN menu_item mi ON ci.menu_item_id = mi.menu_item_id
                   WHERE mi.restaurant_id = %s
                   GROUP BY c.cart_id, u.username, c.status
                   ORDER BY c.cart_id DESC LIMIT 10
                   """, (restaurant_id,))
    recent_orders = cursor.fetchall()

    # Get statistics
    cursor.execute("""
        SELECT SUM(total) as total_revenue,
               COUNT(*)   as total_orders,
               (SELECT username
                FROM user
                WHERE user_id = (SELECT customer_id
                                 FROM cart
                                 WHERE restaurant_id = %s
                                 ORDER BY total DESC LIMIT 1)) as top_customer
        FROM cart
        WHERE restaurant_id = %s
          AND Timestamp >= DATE_SUB(NOW(), INTERVAL 1 MONTH)
    """, (restaurant_id, restaurant_id))

    stats = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        'manage_restaurant.html',
        restaurant=restaurant,
        menu_items=menu_items,
        stats=stats,
        recent_orders=recent_orders  # 👈 Pass this to the template
    )


@app.route('/manager/add_menu_item', methods=['POST'])
def add_menu_item():
    if 'user_id' not in session or session.get('user_type') != 'Manager':
        return redirect(url_for('login'))

    restaurant_id = request.form['restaurant_id']
    name = request.form['name']
    description = request.form['description']
    price = request.form['price']
    image = request.files['image']

    filename = secure_filename(image.filename)
    image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
                   INSERT INTO menu_item
                       (restaurant_id, name, description, price, image_url)
                   VALUES (%s, %s, %s, %s, %s)
                   """, (restaurant_id, name, description, price, filename))

    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('manage_restaurant', restaurant_id=restaurant_id))


# -------------------- Error Handlers --------------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403


@app.route('/decrease_quantity', methods=['POST'])
def decrease_quantity():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    cart_item_id = request.form['cart_item_id']
    conn = get_db_connection()
    cursor = conn.cursor(buffered=True)

    try:
        # cart_item_id kontrolü
        cursor.execute("SELECT quantity FROM cart_item WHERE cart_item_id = %s", (cart_item_id,))
        result = cursor.fetchone()

        if not result:  # Kayıt yoksa
            flash('Item not found in cart!', 'danger')
            return redirect(url_for('view_cart'))

        quantity = result[0]  # Güvenli erişim

        if quantity > 1:
            cursor.execute("UPDATE cart_item SET quantity = quantity - 1 WHERE cart_item_id = %s", (cart_item_id,))
        else:
            cursor.execute("DELETE FROM cart_item WHERE cart_item_id = %s", (cart_item_id,))

        conn.commit()
    except Exception as e:
        conn.rollback()
        flash('An error occurred!', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('view_cart'))


@app.route('/remove_item', methods=['POST'])
def remove_item():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    cart_item_id = request.form['cart_item_id']
    conn = get_db_connection()
    cursor = conn.cursor(buffered=True)

    try:
        # cart_item_id kontrolü
        cursor.execute("SELECT 1 FROM cart_item WHERE cart_item_id = %s", (cart_item_id,))
        if not cursor.fetchone():
            flash('Item not found in cart!', 'danger')
            return redirect(url_for('view_cart'))

        cursor.execute("DELETE FROM cart_item WHERE cart_item_id = %s", (cart_item_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash('An error occurred!', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('view_cart'))


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Kullanıcı ve adres bilgilerini çek
    cursor.execute("SELECT * FROM user WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()

    cursor.execute("SELECT * FROM address WHERE user_id = %s", (user_id,))
    addresses = cursor.fetchall()

    cursor.close()
    conn.close()

    if request.method == 'POST':
        # Form verilerini al
        firstname = request.form['firstname']
        lastname = request.form['lastname']
        username = request.form['username']
        password = request.form['password']
        new_street = request.form['street']
        new_city = request.form['city']

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Kullanıcı bilgilerini güncelle
            cursor.execute("""
                           UPDATE user
                           SET firstname=%s,
                               lastname=%s,
                               username=%s,
                               password=%s
                           WHERE user_id = %s
                           """, (firstname, lastname, username, password, user_id))

            # Yeni adres ekle
            if new_street and new_city:
                cursor.execute("""
                               INSERT INTO address (user_id, street, city)
                               VALUES (%s, %s, %s)
                               """, (user_id, new_street, new_city))

            conn.commit()
            flash('Settings updated successfully!', 'success')
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f'Error: {err}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('settings'))

    return render_template('settings.html', user=user, addresses=addresses)


@app.route('/delete_address/<int:address_id>', methods=['POST'])
def delete_address(address_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kullanıcının adresini kontrol et
        cursor.execute("""
                       SELECT *
                       FROM address
                       WHERE address_id = %s
                         AND user_id = %s
                       """, (address_id, user_id))

        if not cursor.fetchone():
            flash('Bu adresi silme yetkiniz yok!', 'danger')
            return redirect(url_for('settings'))

        # Adresi sil
        cursor.execute("DELETE FROM address WHERE address_id = %s", (address_id,))
        conn.commit()
        flash('Adres başarıyla silindi!', 'success')

    except mysql.connector.Error as err:
        conn.rollback()
        flash(f'Hata: {err}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('settings'))


@app.route('/update_order_status', methods=['POST'])
def update_order_status():
    if 'user_id' not in session or session.get('user_type') != 'Manager':
        return redirect(url_for('login'))

    cart_id = request.form['cart_id']
    new_status = request.form['status']

    conn = get_db_connection()
    cursor = conn.cursor()

    # Optional: Get restaurant_id before update for redirect/back link
    cursor.execute("SELECT restaurant_id FROM cart WHERE cart_id = %s", (cart_id,))
    result = cursor.fetchone()
    restaurant_id = result[0] if result else None

    try:
        cursor.execute("UPDATE cart SET status = %s WHERE cart_id = %s", (new_status, cart_id))
        conn.commit()
    except mysql.connector.Error as err:
        flash(f"Error updating order: {err}", "danger")
        return redirect(url_for('manager_dashboard'))
    finally:
        cursor.close()
        conn.close()

    return render_template("update_order_status.html", cart_id=cart_id, status=new_status, restaurant_id=restaurant_id)




if __name__ == '__main__':
    app.run(debug=True)