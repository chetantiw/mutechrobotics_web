import os
import qrcode
import logging
import re
import urllib.parse
import bleach
from datetime import date
from slugify import slugify  # Install python-slugify

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from psycopg2.extras import RealDictCursor
import psycopg2
from flask_httpauth import HTTPBasicAuth
from dotenv import load_dotenv
from jinja2.exceptions import TemplateNotFound
from werkzeug.routing.exceptions import BuildError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__, template_folder='/root/mutechrobotics_web/mutechrobotics_web/templates', static_folder='/root/mutechrobotics_web/mutechrobotics_web/static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', '84475f41ec790dab85fc91d948936c62')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Load environment variables
load_dotenv()

# Initialize HTTP Basic Auth
auth = HTTPBasicAuth()

# Load admin credentials from environment variables
users = {
    os.getenv("ADMIN_USERNAME", "admin"): os.getenv("ADMIN_PASSWORD", "pass@w0rd")
}

@auth.verify_password
def verify_password(username, password):
    if username in users and users[username] == password:
        return username
    return None

# Database connection function
def get_db_connection():
    conn = psycopg2.connect(
        dbname="mutech_robotics",
        user="mutech_user",
        password=os.getenv("DB_PASSWORD"),
        host="localhost",
    )
    conn.cursor_factory = RealDictCursor  # Use RealDictCursor to return rows as dictionaries
    return conn

# Ensure the upload folder is configured
app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # Corrected unmatched parenthesis

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

@app.route('/admin/upload_image', methods=['POST'])
@auth.login_required
def upload_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return jsonify({'location': url_for('static', filename=f'uploads/{filename}', _scheme='https')})
    return jsonify({'error': 'File not allowed'}), 400

@app.context_processor
def inject_tiny_mce_api_key():
    return {
        'tiny_mce_api_key': os.getenv("TINYMCE_API_KEY", "no-api-key")
    }

# Routes
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Fetch the latest 3 tutorials
    cur.execute("""
        SELECT c.*, t.category, t.difficulty, t.estimated_time
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.content_type = 'tutorial'
        ORDER BY c.created_at DESC
        LIMIT 3
    """)
    latest_tutorials = cur.fetchall()
    # Fetch the latest 3 blog posts
    cur.execute("""
        SELECT c.*, b.author, b.published_date
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.content_type = 'blog'
        ORDER BY b.published_date DESC
        LIMIT 3
    """)
    latest_blogs = cur.fetchall()
    # Fetch upcoming events
    cur.execute("""
        SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost, e.learn
        FROM content c
        JOIN events e ON c.id = e.content_id
        WHERE e.event_date >= CURRENT_DATE
        ORDER BY e.event_date ASC
    """)
    upcoming_events = cur.fetchall()
    for event in upcoming_events:
        event['cost'] = f"₹{event['cost']}"  # Replace dollar with INR symbol
    cur.close()
    conn.close()
    return render_template('index.html', latest_tutorials=latest_tutorials, latest_blogs=latest_blogs, upcoming_events=upcoming_events)

@app.route('/event/<slug>')
def event_details(slug):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost, e.learn
        FROM content c
        JOIN events e ON c.id = e.content_id
        WHERE c.slug = %s
    """, (slug,))
    event = cur.fetchone()
    if event:
        event['cost'] = f"₹{event['cost']}"  # Replace dollar with INR symbol
    if not event:
        cur.close()
        conn.close()
        return render_template('404.html'), 404
    cur.close()
    conn.close()
    return render_template('event-details.html', event=event)

@app.route('/event/<slug>/register', methods=['GET', 'POST'])
def register_for_event(slug):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost, e.learn
            FROM content c
            JOIN events e ON c.id = e.content_id
            WHERE c.slug = %s
        """, (slug,))
        event = cur.fetchone()
        if not event:
            flash('Event not found.', 'error')
            return render_template('404.html'), 404
        if request.method == 'POST':
            name = request.form['name'].strip()
            email = request.form['email'].strip()
            address = request.form['address'].strip()
            contact_number = request.form['contact_number'].strip()
            email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
            contact_number_regex = r'^\d+$'
            if len(name) < 2:
                flash('Name must be at least 2 characters long.', 'error')
            elif not re.match(email_regex, email):
                flash('Please enter a valid email address.', 'error')
            elif len(address) < 5:
                flash('Address must be at least 5 characters long.', 'error')
            elif len(contact_number) < 7:
                flash('Contact number must be at least 7 characters long.', 'error')
            elif not re.match(contact_number_regex, contact_number):
                flash('Contact number must contain only digits.', 'error')
            else:
                try:
                    cur.execute("""
                        INSERT INTO event_registrations (event_id, name, email, address, contact_number, payment_status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (event['id'], name, email, address, contact_number, 'pending_verification'))
                    registration_id = cur.fetchone()['id']
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash('You are already registered for this event.', 'error')
                    return render_template('event-registration.html', event=event)
                except psycopg2.Error as e:
                    conn.rollback()
                    logger.error(f"Database error: {str(e)}", exc_info=True)
                    flash(f'Error registering for the event: {str(e)}', 'error')
                    return render_template('event-registration.html', event=event)
                STATIC_QR_DIR = os.path.join(app.static_folder, 'qr_codes')
                if not os.path.exists(STATIC_QR_DIR):
                    os.makedirs(STATIC_QR_DIR, exist_ok=True)
                try:
                    cost = float(event['cost'])
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid cost value: {event['cost']}. Error: {str(e)}")
                    flash('Error: Invalid event cost. Please contact support.', 'error')
                    return render_template('event-registration.html', event=event)
                upi_link = f"upi://pay?pa=chetantiwari09@ibl&pn=MuTechRobotics&am={cost:.2f}&cu=INR&tn=EventRegistration_{registration_id}&tr={registration_id}"
                qr = qrcode.QRCode(version=1, box_size=10, border=5)
                qr.add_data(upi_link)
                qr.make(fit=True)
                img = qr.make_image(fill='black', back_color='white')
                upi_qr_path = os.path.join(STATIC_QR_DIR, f"upi_registration_{registration_id}.png")
                img.save(upi_qr_path)
                whatsapp_number = "+919301921013"
                whatsapp_message = f"I have made a payment for the Event with registration ID: {registration_id}. Here is the payment screenshot."
                encoded_whatsapp_message = urllib.parse.quote(whatsapp_message)
                whatsapp_link = f"https://wa.me/{whatsapp_number}?text={encoded_whatsapp_message}"
                qr = qrcode.QRCode(version=1, box_size=10, border=5)
                qr.add_data(whatsapp_link)
                qr.make(fit=True)
                img = qr.make_image(fill='black', back_color='white')
                whatsapp_qr_path = os.path.join(STATIC_QR_DIR, f"whatsapp_registration_{registration_id}.png")
                img.save(whatsapp_qr_path)
                flash('Registration successful! Please scan the UPI QR code to make the payment, then share the payment screenshot via WhatsApp.', 'success')
                upi_qr_url = f"/static/qr_codes/upi_registration_{registration_id}.png?v={registration_id}"
                whatsapp_qr_url = f"/static/qr_codes/whatsapp_registration_{registration_id}.png?v={registration_id}"
                return render_template(
                    'upi-payment.html',
                    event=event,
                    registration_id=registration_id,
                    upi_qr_path=upi_qr_url,
                    whatsapp_qr_path=whatsapp_qr_url,
                    whatsapp_link=whatsapp_link,
                )
        return render_template('event-registration.html', event=event)
    finally:
        cur.close()
        conn.close()

@app.route('/events')
def events():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost
        FROM content c
        JOIN events e ON c.id = e.content_id
        WHERE c.content_type = 'event'
        ORDER BY e.event_date ASC
    """)
    events = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('events.html', events=events)

@app.route('/tutorial/<slug>')
def tutorial_details(slug):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Fetch the tutorial details
    cur.execute("""
        SELECT c.*, t.category, t.difficulty, t.estimated_time
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.content_type = 'tutorial' AND c.slug = %s
    """, (slug,))
    tutorial = cur.fetchone()
    if not tutorial:
        cur.close()
        conn.close()
        return render_template('404.html'), 404
    # Fetch tutorial sections
    cur.execute("""
        SELECT title, html_content, section_order
        FROM tutorial_sections
        WHERE tutorial_id = %s
        ORDER BY section_order
    """, (tutorial['id'],))
    tutorial['sections'] = cur.fetchall()
    # Fetch related tutorials
    cur.execute("""
        SELECT c.title, c.slug
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.content_type = 'tutorial' AND t.category = %s AND c.id != %s
        LIMIT 3
    """, (tutorial['category'], tutorial['id']))
    related_tutorials = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('tutorial-details.html', tutorial=tutorial, related_tutorials=related_tutorials)

@app.route('/tutorials')
def tutorials():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.*, t.category, t.difficulty, t.estimated_time
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.content_type = 'tutorial'
        ORDER BY c.created_at DESC
    """)
    tutorials = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('tutorials.html', tutorials=tutorials)

@app.route('/blog/<slug>')
def blog_details(slug):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Fetch the blog post
    cur.execute("""
        SELECT c.*, b.author, b.published_date
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.content_type = 'blog' AND c.slug = %s
    """, (slug,))
    blog = cur.fetchone()
    if not blog:
        cur.close()
        conn.close()
        return render_template('404.html'), 404
    # Fetch related blogs (e.g., 3 other recent blogs excluding the current one)
    cur.execute("""
        SELECT c.title, c.slug
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.content_type = 'blog' AND c.slug != %s
        ORDER BY b.published_date DESC
        LIMIT 3
    """, (slug,))
    related_blogs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('blog-details.html', blog=blog, related_blogs=related_blogs)

@app.route('/blog')
def blog():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.*, b.author, b.published_date
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.content_type = 'blog'
        ORDER BY b.published_date DESC
    """)
    blogs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('blog.html', blogs=blogs)

@app.route('/resources')
def resources():
    return render_template('resources.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        message = request.form['message']
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO contact_messages (name, email, message)
                VALUES (%s, %s, %s)
            """, (name, email, message))
            conn.commit()
            cur.close()
            conn.close()
            flash('Your message has been sent successfully!', 'success')
        except psycopg2.Error as e:
            flash(f'Error saving your message: {e}', 'error')
        return redirect(url_for('contact'))
    return render_template('contact.html')

# Admin Routes
@app.route('/admin')
@auth.login_required
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost
        FROM content c
        JOIN events e ON c.id = e.content_id
        WHERE c.content_type = 'event'
        ORDER BY e.event_date ASC
    """)
    events = cur.fetchall()
    event_registrations = {}
    for event in events:
        cur.execute("""
            SELECT id, name, email, address, contact_number, registered_at
            FROM event_registrations
            WHERE event_id = %s
            ORDER BY registered_at DESC
        """, (event['id'],))
        event_registrations[event['id']] = cur.fetchall()
    cur.execute("""
        SELECT c.*, t.category, t.difficulty, t.estimated_time
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.content_type = 'tutorial'
        ORDER BY c.created_at DESC
    """)
    tutorials = cur.fetchall()
    cur.execute("""
        SELECT c.*, b.author, b.published_date
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.content_type = 'blog'
        ORDER BY b.published_date DESC
    """)
    blogs = cur.fetchall()
    cur.execute("""
        SELECT id, name, email, message, submitted_at
        FROM contact_messages
        ORDER BY submitted_at DESC
    """)
    messages = cur.fetchall()
    cur.execute("""
        SELECT er.id, er.event_id, er.name, er.email, er.address, er.contact_number, er.payment_status, c.title AS event_title
        FROM event_registrations er
        JOIN content c ON er.event_id = c.id
        ORDER BY er.id DESC
    """)
    registrations = cur.fetchall()
    tiny_mce_api_key = os.getenv("TINYMCE_API_KEY", "no-api-key")
    cur.close()
    conn.close()
    return render_template(
        'admin/dashboard.html',
        events=events,
        event_registrations=event_registrations,
        tutorials=tutorials,
        blogs=blogs,
        messages=messages,
        registrations=registrations,
        tiny_mce_api_key=tiny_mce_api_key
    )

@app.route('/admin/add-event', methods=['GET', 'POST'])
@auth.login_required
def add_event():
    if request.method == 'POST':
        title = request.form['title']
        tagline = request.form['tagline']
        overview = request.form['overview']
        slug = request.form['slug']
        event_date = request.form['event_date']
        event_time = request.form['event_time']
        location = request.form['location']
        duration = request.form['duration']
        cost = request.form['cost']
        learn = request.form['learn'].split(',')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO content (content_type, title, tagline, overview, slug)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, ('event', title, tagline, overview, slug))
        content_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO events (content_id, event_date, event_time, location, duration, cost, learn)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (content_id, event_date, event_time, location, duration, cost, learn))
        conn.commit()
        cur.close()
        conn.close()
        flash('Event added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/add_event.html')

@app.route('/admin/edit-event/<int:id>', methods=['GET', 'POST'])
@auth.login_required
def edit_event(id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        title = request.form['title']
        tagline = request.form['tagline']
        overview = request.form['overview']
        slug = request.form['slug']
        event_date = request.form['event_date']
        event_time = request.form['event_time']
        location = request.form['location']
        duration = request.form['duration']
        cost = request.form['cost']
        learn = request.form['learn'].split(',')
        cur.execute("""
            UPDATE content
            SET title = %s, tagline = %s, overview = %s, slug = %s
            WHERE id = %s
        """, (title, tagline, overview, slug, id))
        cur.execute("""
            UPDATE events
            SET event_date = %s, event_time = %s, location = %s, duration = %s, cost = %s, learn = %s
            WHERE content_id = %s
        """, (event_date, event_time, location, duration, cost, learn, id))
        conn.commit()
        cur.close()
        conn.close()
        flash('Event updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    cur.execute("""
        SELECT c.*, e.event_date, e.event_time, e.location, e.duration, e.cost, e.learn
        FROM content c
        JOIN events e ON c.id = e.content_id
        WHERE c.id = %s
    """, (id,))
    event = cur.fetchone()
    if not event:
        cur.close()
        conn.close()
        flash('Event not found!', 'error')
        return redirect(url_for('admin_dashboard'))
    cur.close()
    conn.close()
    return render_template('admin/edit_event.html', event=event)

@app.route('/admin/delete-event/<int:id>')
@auth.login_required
def delete_event(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM events WHERE content_id = %s", (id,))
    cur.execute("DELETE FROM content WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Event deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-tutorial', methods=['GET', 'POST'])
@auth.login_required
def add_tutorial():
    if request.method == 'POST':
        logger.info("Add Tutorial POST request received")
        try:
            # Log the received form data
            logger.info(f"Form Data: {request.form}")
            # Extract form data
            title = request.form.get('title', '').strip()
            tagline = request.form.get('tagline', '').strip()
            overview = request.form.get('overview', '').strip()
            slug = slugify(title)  # Auto-generate slug from title
            category = request.form.get('category', '').strip()
            difficulty = request.form.get('difficulty', '').strip()
            estimated_time = request.form.get('estimated_time', '').strip()
            section_titles = request.form.getlist('section_titles[]')
            section_html_contents = request.form.getlist('section_html_contents[]')
            section_orders = request.form.getlist('section_orders[]')
            # Validate required fields
            if not title or not tagline or not overview or not slug or not category or not difficulty or not estimated_time:
                logger.error("Validation Error: Missing required fields")
                flash("All fields are required. Please fill out the form completely.", "error")
                return redirect(url_for('add_tutorial'))
            # Log the extracted data
            logger.info(f"Extracted Data: title={title}, tagline={tagline}, slug={slug}, category={category}, difficulty={difficulty}, estimated_time={estimated_time}")
            logger.info(f"Sections: titles={section_titles}, contents={section_html_contents}, orders={section_orders}")
            # Database operations
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                # Insert into content table
                cur.execute("""
                    INSERT INTO content (content_type, title, tagline, overview, slug)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, ('tutorial', title, tagline, overview, slug))
                content_id = cur.fetchone()['id']  # Access the 'id' key from the dictionary
                logger.info(f"Inserted content with ID: {content_id}")
                # Insert into tutorials table
                cur.execute("""
                    INSERT INTO tutorials (content_id, category, difficulty, estimated_time)
                    VALUES (%s, %s, %s, %s)
                """, (content_id, category, difficulty, estimated_time))
                logger.info(f"Inserted tutorial details for content ID: {content_id}")
                # Insert sections
                for title, html_content, order in zip(section_titles, section_html_contents, section_orders):
                    cur.execute("""
                        INSERT INTO tutorial_sections (tutorial_id, title, html_content, section_order)
                        VALUES (%s, %s, %s, %s)
                    """, (content_id, title, html_content, order))
                    logger.info(f"Inserted section: title={title}, order={order}")
                # Commit the transaction
                conn.commit()
                logger.info("Transaction committed successfully")
                flash('Tutorial added successfully!', 'success')
            except Exception as db_error:
                conn.rollback()
                logger.error(f"Database Error: {db_error}")
                flash(f"Error adding tutorial: {db_error}", "error")
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.error(f"Unexpected Error: {e}", exc_info=True)
            flash(f"An unexpected error occurred: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/add_tutorial.html')

@app.route('/admin/edit-tutorial/<int:id>', methods=['GET', 'POST'])
@auth.login_required
def edit_tutorial(id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        title = request.form['title']
        tagline = request.form['tagline']
        overview = request.form['overview']
        slug = request.form['slug']
        category = request.form['category']
        difficulty = request.form['difficulty']
        estimated_time = request.form['estimated_time']
        section_titles = request.form.getlist('section_titles[]')
        section_html_contents = request.form.getlist('section_html_contents[]')  # Updated to handle HTML content
        section_orders = request.form.getlist('section_orders[]')
        try:
            cur.execute("""
                UPDATE content
                SET title = %s, tagline = %s, overview = %s, slug = %s
                WHERE id = %s
            """, (title, tagline, overview, slug, id))
            cur.execute("""
                UPDATE tutorials
                SET category = %s, difficulty = %s, estimated_time = %s
                WHERE content_id = %s
            """, (category, difficulty, estimated_time, id))
            cur.execute("DELETE FROM tutorial_sections WHERE tutorial_id = %s", (id,))
            for title, html_content, order in zip(section_titles, section_html_contents, section_orders):
                cur.execute("""
                    INSERT INTO tutorial_sections (tutorial_id, title, html_content, section_order)
                    VALUES (%s, %s, %s, %s)
                """, (id, title, html_content, order))
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            flash(f"Error updating tutorial: {e}", "error")
        finally:
            cur.close()
            conn.close()
        flash('Tutorial updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    cur.execute("""
        SELECT c.*, t.category, t.difficulty, t.estimated_time
        FROM content c
        JOIN tutorials t ON c.id = t.content_id
        WHERE c.id = %s
    """, (id,))
    tutorial = cur.fetchone()
    cur.execute("""
        SELECT title, html_content, section_order
        FROM tutorial_sections
        WHERE tutorial_id = %s
        ORDER BY section_order
    """, (id,))
    tutorial['sections'] = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin/edit_tutorial.html', tutorial=tutorial)

@app.route('/admin/delete-tutorial/<int:id>')
@auth.login_required
def delete_tutorial(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tutorials WHERE content_id = %s", (id,))
    cur.execute("DELETE FROM content WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Tutorial deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-blog', methods=['GET', 'POST'])
@auth.login_required
def add_blog():
    if request.method == 'POST':
        title = request.form['title']
        tagline = request.form['tagline']
        overview = request.form['overview']
        content = request.form['content']  # This will now contain HTML from TinyMCE
        author = request.form['author']
        published_date = request.form['published_date']
        image = request.files.get('image')
        # Sanitize the HTML content
        allowed_tags = [
            'p', 'br', 'strong', 'em', 'b', 'i', 'u', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'ul', 'ol', 'li', 'blockquote', 'code', 'pre', 'img', 'div', 'span', 'table', 'tr', 'td', 'th'
        ]
        allowed_attributes = {
            'a': ['href', 'title'],
            'img': ['src', 'alt', 'style'],
            'div': ['style'],
            'span': ['style'],
        }
        content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attributes, strip=True)
        # Generate a slug from the title
        slug = title.lower().replace(' ', '-').replace('/', '-')
        # Handle image upload
        image_url = None
        if image and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            image_url = url_for('static', filename=f'uploads/{filename}', _scheme='https')
        # Insert into the database
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO content (title, tagline, overview, content, content_type, slug, image_url)
                VALUES (%s, %s, %s, %s, 'blog', %s, %s)
                RETURNING id
            """, (title, tagline, overview, content, slug, image_url))
            content_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO blogs (content_id, author, published_date)
                VALUES (%s, %s, %s)
            """, (content_id, author, published_date))
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            logger.error(f"Database error while adding blog: {str(e)}", exc_info=True)
            flash(f"Error adding blog post: {str(e)}", "error")
            return render_template('admin/add_blog.html')
        finally:
            cur.close()
            conn.close()
        flash('Blog post added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/add_blog.html')

@app.route('/admin/edit-blog/<int:id>', methods=['GET', 'POST'])
@auth.login_required
def edit_blog(id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        title = request.form['title']
        tagline = request.form['tagline']
        overview = request.form['overview']
        content = request.form['content']  # This will now contain HTML from TinyMCE
        author = request.form['author']
        published_date = request.form['published_date']
        image = request.files.get('image')
        # Sanitize the HTML content
        allowed_tags = [
            'p', 'br', 'strong', 'em', 'b', 'i', 'u', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'ul', 'ol', 'li', 'blockquote', 'code', 'pre', 'img', 'div', 'span', 'table', 'tr', 'td', 'th'
        ]
        allowed_attributes = {
            'a': ['href', 'title'],
            'img': ['src', 'alt', 'style'],
            'div': ['style'],
            'span': ['style'],
        }
        content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attributes, strip=True)
        # Generate a new slug from the updated title
        slug = title.lower().replace(' ', '-').replace('/', '-')
        # Fetch the current blog to get the existing image_url
        cur.execute("""
            SELECT c.*, b.author, b.published_date
            FROM content c
            JOIN blogs b ON c.id = b.content_id
            WHERE c.id = %s
        """, (id,))
        blog = cur.fetchone()
        # Handle image upload (only if a new image is provided)
        image_url = blog['image_url']
        if image and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            image_url = url_for('static', filename=f'uploads/{filename}', _scheme='https')
        # Update the database
        try:
            cur.execute("""
                UPDATE content
                SET title = %s, tagline = %s, overview = %s, content = %s, slug = %s, image_url = %s
                WHERE id = %s
            """, (title, tagline, overview, content, slug, image_url, id))
            cur.execute("""
                UPDATE blogs
                SET author = %s, published_date = %s
                WHERE content_id = %s
            """, (author, published_date, id))
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            logger.error(f"Database error while editing blog: {str(e)}", exc_info=True)
            flash(f"Error editing blog post: {str(e)}", "error")
            return render_template('admin/edit_blog.html', blog=blog)
        finally:
            cur.close()
            conn.close()
        flash('Blog post updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    cur.execute("""
        SELECT c.*, b.author, b.published_date
        FROM content c
        JOIN blogs b ON c.id = b.content_id
        WHERE c.id = %s
    """, (id,))
    blog = cur.fetchone()
    if not blog:
        cur.close()
        conn.close()
        flash('Blog post not found!', 'error')
        return redirect(url_for('admin_dashboard'))
    cur.close()
    conn.close()
    return render_template('admin/edit_blog.html', blog=blog)

@app.route('/admin/delete-blog/<int:id>')
@auth.login_required
def delete_blog(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM blogs WHERE content_id = %s", (id,))
    cur.execute("DELETE FROM content WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Blog post deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/registrations')
@auth.login_required
def admin_registrations():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT er.id, er.event_id, er.name, er.email, er.address, er.contact_number, er.payment_status, c.title AS event_title
        FROM event_registrations er
        JOIN content c ON er.event_id = c.id
        ORDER BY er.id DESC
    """)
    registrations = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_registrations.html', registrations=registrations)

@app.route('/admin/update-registration-status/<int:registration_id>/<status>', methods=['POST'])
@auth.login_required
def update_registration_status(registration_id, status):
    if status not in ['pending_verification', 'confirmed', 'failed']:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin_registrations'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            UPDATE event_registrations
            SET payment_status = %s
            WHERE id = %s
        """, (status, registration_id))
        if cur.rowcount == 0:
            conn.rollback()
            flash(f'Registration ID {registration_id} not found.', 'error')
        else:
            conn.commit()
            flash(f'Registration {registration_id} status updated to {status}.', 'success')
    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"Database error: {str(e)}", exc_info=True)
        flash(f'Error updating registration status: {str(e)}', 'error')
    cur.close()
    conn.close()
    return redirect(url_for('admin_registrations'))

@app.route('/admin/delete-registration/<int:id>')
@auth.login_required
def delete_registration(id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM event_registrations WHERE id = %s", (id,))
        if cur.rowcount == 0:
            flash('Registration not found!', 'error')
        else:
            conn.commit()
            flash('Registration deleted successfully!', 'success')
        cur.close()
        conn.close()
    except psycopg2.Error as e:
        flash(f'Error deleting registration: {e}', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-message/<int:id>')
@auth.login_required
def delete_message(id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM contact_messages WHERE id = %s", (id,))
        if cur.rowcount == 0:
            flash('Message not found!', 'error')
        else:
            conn.commit()
            flash('Contact message deleted successfully!', 'success')
        cur.close()
        conn.close()
    except psycopg2.Error as e:
        logger.error(f"Database error while deleting message ID {id}: {e}")
        flash(f'Error deleting message: {e}', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
@auth.login_required
def admin_logout():
    return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404