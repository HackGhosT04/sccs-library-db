from flask import Flask, request, jsonify,g, abort, send_file, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, date, time,timedelta,timezone
import uuid
import os
import json
import firebase_admin
from firebase_admin import credentials, auth, db as firebase_db
from sqlalchemy import func, and_, or_
from werkzeug.exceptions import NotFound, Unauthorized, Forbidden
from sqlalchemy.exc import SQLAlchemyError
import traceback
from sqlalchemy.dialects.mysql import LONGBLOB
import base64
from extensions import db
from sqlalchemy import Table
from werkzeug.utils import secure_filename
from flask import send_from_directory

 
app = Flask(__name__)
CORS(app,supports_credentials=True, resources={r"/*": {"origins": "*"}})


# Configuration - Use environment variables in production
DATABASE_URI = os.environ.get('SQLALCHEMY_DATABASE_URI')
if not DATABASE_URI:
    raise RuntimeError("SQLALCHEMY_DATABASE_URI env var is required")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI


app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET', 'super-secret-key')
db.init_app(app)

# Initialize Firebase
# Load the JSON string from the environment
firebase_json = os.environ["FIREBASE_SERVICE_ACCOUNT"]

# Parse it into a dict
cred_dict = json.loads(firebase_json)

basedir = os.path.dirname(os.path.abspath(__file__))
app.config.setdefault('MEDIA_UPLOAD_FOLDER', os.path.join(basedir, 'uploads', 'media'))
os.makedirs(app.config['MEDIA_UPLOAD_FOLDER'], exist_ok=True)


# Create credentials and initialize
if not firebase_admin._apps:
    # Pull the JSON from env var
    firebase_json = os.environ["FIREBASE_SERVICE_ACCOUNT"]
    cred_dict     = json.loads(firebase_json)
    # Use it to create the credential
    cred          = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://sccs-a27f7-default-rtdb.firebaseio.com/'
    })



# --- Authentication Middleware ---
@app.before_request
def authenticate_request():
    if request.method == 'OPTIONS':
        return
    # Skip authentication for public endpoints
    public_routes = ['register_user','update_computer','list_computers','add_book','update_book_status','update_book','search_books','get_rooms','update_seat','create_seat','seat_availability','bulk_update_hours','update_hours','get_announcements','delete_announcement','create_announcement', 'get_hours', 'search_books']
    if request.endpoint in public_routes:
        return

    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise Unauthorized('Missing or invalid Authorization header')

    token = auth_header.split(' ')[1]
    try:
        decoded_token = auth.verify_id_token(token)
        request.firebase_uid = decoded_token['uid']
        # Get corresponding user from database
        user = User.query.filter_by(firebase_uid=request.firebase_uid).first()
        if not user:
            raise NotFound('User not found in database')
        g.current_user = user
    except Exception as e:
        raise Unauthorized(f'Invalid token: {str(e)}')


# --- Database Models ---

class User(db.Model):
    __tablename__ = 'user'
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(256), unique=True, nullable=False)
    role = db.Column(db.Enum('student', 'staff'), nullable=False, default='student')
    
    # Relationships
    reservations = db.relationship('Reservation', backref='user', lazy=True)
    loans = db.relationship('Loan', backref='user', lazy=True)
    fees = db.relationship('FeeFine', backref='user', lazy=True)
    appointments = db.relationship('Appointment', foreign_keys='Appointment.user_id', backref='user', lazy=True)
    purchase_requests = db.relationship('PurchaseRequest', backref='user', lazy=True)
    recommendations = db.relationship('Recommendation', backref='user', lazy=True)

class Room(db.Model):
    __tablename__ = 'room'
    room_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    library_id = db.Column(db.Integer, db.ForeignKey('library.library_id'), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    room_type = db.Column(db.String(20), nullable=False) 
    seats = db.relationship('Seat', backref='room', lazy=True)

class Seat(db.Model):
    __tablename__ = 'seat'
    seat_id     = db.Column(db.Integer, primary_key=True, autoincrement=True)
    room_id     = db.Column(db.Integer, db.ForeignKey('room.room_id'), nullable=False)
    identifier  = db.Column(db.String(64), nullable=False)
    is_computer = db.Column(db.Boolean, default=False)
    is_active   = db.Column(db.Boolean, default=True)
    is_occupied = db.Column(db.Boolean, default=False)
    specs       = db.Column(db.String(256), default='Standard specs')


class Book(db.Model):
    __tablename__ = 'book'
    book_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    isbn = db.Column(db.String(32), unique=True, nullable=False)
    title = db.Column(db.String(512), nullable=False)
    author = db.Column(db.String(256), nullable=False)
    publisher = db.Column(db.String(256))
    year = db.Column(db.Integer)
    copies_total = db.Column(db.Integer, default=1)
    copies_available = db.Column(db.Integer, default=1)
    image= db.Column(LONGBLOB, nullable=True)  
    
    # Relationships
    reservations = db.relationship('Reservation', backref='book', lazy=True)
    loans = db.relationship('Loan', backref='book', lazy=True)

class Reservation(db.Model):
    __tablename__ = 'reservation'
    reservation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('book.book_id'), nullable=False)
    library_id = db.Column(db.Integer, db.ForeignKey('library.library_id'), nullable=False)
    reserved_from = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reserved_until = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.Enum('active', 'cancelled', 'fulfilled'), default='active')

class Loan(db.Model):
    __tablename__ = 'loan'
    loan_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('book.book_id'), nullable=False)
    checkout_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    returned_date = db.Column(db.Date)

class FeeFine(db.Model):
    __tablename__ = 'feefine'
    feefine_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    amount = db.Column(db.Numeric(8,2), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.Enum('unpaid', 'paid'), default='unpaid')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Announcement(db.Model):
    __tablename__ = 'announcement'
    announcement_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(256), nullable=False)
    body = db.Column(db.Text, nullable=False)
    posted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

class Appointment(db.Model):
    __tablename__ = 'appointment'
    appointment_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    librarian_user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    library_id = db.Column(db.Integer, db.ForeignKey('library.library_id'), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.Enum('pending','confirmed','cancelled','completed', name='appointment_status_enum'), default='pending')
    notes = db.Column(db.Text)

    # Relationships
    librarian = db.relationship('User', foreign_keys=[librarian_user_id])

class PurchaseRequest(db.Model):
    __tablename__ = 'purchaserequest'
    request_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    title = db.Column(db.String(512), nullable=False)
    author = db.Column(db.String(256), nullable=False)
    isbn = db.Column(db.String(32))
    justification = db.Column(db.Text)
    status = db.Column(db.Enum('open','ordered','declined','received', name='purchase_status_enum'), default='open')
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

class Recommendation(db.Model):
    __tablename__ = 'recommendation'
    rec_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    category = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.Enum('new','reviewed','implemented','rejected', name='recommendation_status_enum'), default='new')

    
class OperatingTime(db.Model):
    __tablename__ = 'operatingtime'
    operating_time_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    library_id        = db.Column(db.Integer, db.ForeignKey('library.library_id'), nullable=False)
    weekday           = db.Column(db.Enum('Mon','Tue','Wed','Thu','Fri','Sat','Sun', name='weekday_enum'), nullable=False)
    open_time         = db.Column(db.Time, nullable=False)
    close_time        = db.Column(db.Time, nullable=False)

class Library(db.Model):
    __tablename__ = 'library'
    library_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(256), nullable=False)
    location = db.Column(db.String(256), nullable=False)
    type = db.Column(db.String(64), nullable=False, default='Information Center')
    
    # Relationships
    operating_hours = db.relationship('OperatingTime', backref='library', lazy=True)

class StudyRoom(db.Model):
    __tablename__ = 'study_room'
    room_id        = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(255), nullable=False)
    description    = db.Column(db.Text)
    subject        = db.Column(db.String(100))
    capacity       = db.Column(db.Integer, default=10)
    created_by     = db.Column(db.Integer, db.ForeignKey('user.user_id'))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    is_active      = db.Column(db.Boolean, default=True)

class StudyRoomMember(db.Model):
    __tablename__ = 'study_room_member'
    member_id      = db.Column(db.Integer, primary_key=True)
    room_id        = db.Column(db.Integer, db.ForeignKey('study_room.room_id'))
    user_id        = db.Column(db.Integer, db.ForeignKey('user.user_id'))
    student_number = db.Column(db.String(50))
    student_email  = db.Column(db.String(255))
    status         = db.Column(db.Enum('pending','approved','rejected',name='membership_status_enum'), default='pending')
    joined_at      = db.Column(db.DateTime)

    # ← ADD THIS:
    user = db.relationship(
        'User',
        backref=db.backref('study_memberships', lazy='dynamic'),
        lazy='joined'
    )


class StudyRoomMedia(db.Model):
    __tablename__ = 'study_room_media'
    media_id       = db.Column(db.Integer, primary_key=True)
    room_id        = db.Column(db.Integer, db.ForeignKey('study_room.room_id'))
    user_id        = db.Column(db.Integer, db.ForeignKey('user.user_id'))
    file_name      = db.Column(db.String(255))
    file_type      = db.Column(db.String(50))
    file_path      = db.Column(db.String(512))
    uploaded_at    = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship(
        'User',
        backref=db.backref('media_uploads', lazy='dynamic'),
        lazy='joined'
    )

class StudyRoomMindMap(db.Model):
    __tablename__ = 'study_room_mindmap'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('study_room.room_id'), unique=True)
    data = db.Column(db.JSON)  # Stores nodes and connections
# Create tables
with app.app_context():
    db.create_all()

    if Library.query.count() == 0:
        default_libraries = [
            {
                "name":     "Thoko Mayekiso",
                "location": "Mbombela Mian campus",
                "type":     "Information Center"
            }
        ]

        for lib_def in default_libraries:
            db.session.add(Library(**lib_def))

        db.session.commit()
        print("🌱 Seeded 2 default libraries")
    else:
        print(f"✅ {Library.query.count()} libraries already present, skipping seed")
        
def initialize_library(library_id=1):
    # Create default rooms if they don't exist
    rooms = [
        {"name": "library-lab01", "type": "computer_lab"},
        {"name": "library-lab02", "type": "computer_lab"},
        {"name": "library-lab03", "type": "computer_lab"},
        {"name": "library-lab04", "type": "computer_lab"},
        {"name": "studyarea", "type": "study_room"},
    ]
    
    for room_data in rooms:
        room = Room.query.filter_by(
            library_id=library_id,
            name=room_data["name"]
        ).first()
        
        if not room:
            room = Room(
                library_id=library_id,
                name=room_data["name"],
                room_type=room_data["type"]
            )
            db.session.add(room)
            db.session.commit()
            
            # Create seats for this room
            seat_count = 50 if "lab" in room_data["name"] else 100
            prefix = "Slab" if "lab" in room_data["name"] else "SSlib-"
            
            for i in range(1, seat_count + 1):
                identifier = f"{prefix}{i:02d}" if "lab" in room_data["name"] else f"{prefix}{i}"
                
                seat = Seat(
                    room_id=room.room_id,
                    identifier=identifier,
                    is_computer=("lab" in room_data["name"]),
                    is_active=True,
                    is_occupied=False
                )
                db.session.add(seat)
    
    db.session.commit()

# --- API Endpoints ---

# 1. Seat Availability
@app.route('/libraries/<int:library_id>/seats/availability', methods=['GET'])
def seat_availability(library_id):
    is_computer = request.args.get('is_computer', type=str)
    room_id = request.args.get('room_id', type=int)
    active_only = request.args.get('active', 'true') == 'true'
    
    # Join with Room to filter by library
    query = Seat.query.join(Room).filter(Room.library_id == library_id)
    
    # Add room filter if provided
    if room_id:
        query = query.filter(Seat.room_id == room_id)
    
    if is_computer and is_computer.lower() in ['true', 'false']:
        query = query.filter(Seat.is_computer == (is_computer.lower() == 'true'))
    if active_only:
        query = query.filter(Seat.is_active == True)
    
    seats = query.all()
    return jsonify([{
        'seat_id': s.seat_id,
        'identifier': s.identifier,
        'is_computer': s.is_computer,
        'is_active': s.is_active,
        'is_occupied': s.is_occupied,
        'room_id': s.room_id
    } for s in seats])

# Create Seat
@app.route('/libraries/<int:library_id>/seats', methods=['POST'])
def create_seat(library_id):
    data = request.get_json() or {}
    room_id = data.get('room_id')
    identifier = data.get('identifier')
    is_computer = data.get('is_computer', False)
    is_active = data.get('is_active', True)
    is_occupied = data.get('is_occupied', False)

    if not room_id or not identifier:
        abort(400, description="Both 'room_id' and 'identifier' are required")

    # Verify room belongs to library
    room = Room.query.filter_by(room_id=room_id, library_id=library_id).first()
    if not room:
        abort(400, description="Invalid room_id for this library")

    s = Seat(
        room_id=room_id,
        identifier=identifier,
        is_computer=bool(is_computer),
        is_active=bool(is_active),
        is_occupied=bool(is_occupied)
    )
    db.session.add(s)
    db.session.commit()

    return jsonify({
        'seat_id': s.seat_id,
        'identifier': s.identifier,
        'is_computer': s.is_computer,
        'is_active': s.is_active,
        'is_occupied': s.is_occupied,
        'room_id': s.room_id
    }), 201

# Update Seat
@app.route('/libraries/<int:library_id>/seats/<int:seat_id>', methods=['PUT'])
def update_seat(library_id, seat_id):
    data = request.get_json() or {}
    s = Seat.query.get_or_404(seat_id)
    
    # Verify seat belongs to library
    room = Room.query.filter_by(room_id=s.room_id, library_id=library_id).first()
    if not room:
        abort(404)

    if 'identifier' in data:
        s.identifier = data['identifier']
    if 'is_computer' in data:
        s.is_computer = bool(data['is_computer'])
    if 'is_active' in data:
        s.is_active = bool(data['is_active'])
    if 'is_occupied' in data:
        s.is_occupied = bool(data['is_occupied'])
    if 'room_id' in data:
        # Verify new room belongs to library
        new_room = Room.query.filter_by(room_id=data['room_id'], library_id=library_id).first()
        if new_room:
            s.room_id = data['room_id']

    db.session.commit()
    return jsonify({
        'seat_id': s.seat_id,
        'identifier': s.identifier,
        'is_computer': s.is_computer,
        'is_active': s.is_active,
        'is_occupied': s.is_occupied,
        'room_id': s.room_id
    }), 200

# 2. Lab List
@app.route('/libraries/labs', methods=['GET'])
def lab_list():
    labs = Library.query.filter_by(type='lab').all()
    return jsonify([{
        'library_id': l.library_id,
        'name': l.name,
        'location': l.location
    } for l in labs])

# 3. Book Search
@app.route('/books', methods=['GET'])
def search_books():
    search_term = request.args.get('q', '').strip()
    page        = request.args.get('page', 1, type=int)
    per_page    = 10

    qry = Book.query
    if search_term:
        qry = qry.filter(
            or_(
                Book.isbn.ilike(f'%{search_term}%'),
                Book.title.ilike(f'%{search_term}%'),
                Book.author.ilike(f'%{search_term}%')
            )
        )

    paginated = qry.paginate(page=page, per_page=per_page, error_out=False)

    items = []
    for b in paginated.items:
        # base64 encode the binary if present
        img_b64 = base64.b64encode(b.image).decode('ascii') if b.image else None

        items.append({
            'book_id':          b.book_id,
            'isbn':             b.isbn,
            'title':            b.title,
            'author':           b.author,
            'copies_available': b.copies_available,
            'image_base64':     img_b64
        })

    return jsonify({
        'items':    items,
        'total':    paginated.total,
        'page':     paginated.page,
        'per_page': paginated.per_page,
        'pages':    paginated.pages
    })



@app.route('/books', methods=['POST'])
def add_book():
    # Grab raw inputs
    isbn_raw      = request.form.get('isbn', '').strip()
    title_raw     = request.form.get('title', '').strip()
    author_raw    = request.form.get('author', '').strip()
    publisher_raw = request.form.get('publisher', '').strip() or None
    year_raw      = request.form.get('year', '').strip()
    copies_raw    = request.form.get('copies_total', '').strip()
    image_file    = request.files.get('image')

    # Validate
    if not isbn_raw:
        return jsonify({'error': 'ISBN is required'}), 400
    if not title_raw:
        return jsonify({'error': 'Title is required'}), 400
    if not author_raw:
        return jsonify({'error': 'Author is required'}), 400
    if not copies_raw.isdigit() or int(copies_raw) < 1:
        return jsonify({'error': 'copies_total must be a positive integer'}), 400

    # Parse year
    year_int = None
    if year_raw:
        if not year_raw.isdigit():
            return jsonify({'error': 'Year must be an integer'}), 400
        year_int = int(year_raw)

    copies = int(copies_raw)

    # Debug: dump incoming data to your logs
    app.logger.debug(f"ADDING BOOK → isbn={isbn_raw!r}, title={title_raw!r}, author={author_raw!r}, "
                     f"publisher={publisher_raw!r}, year={year_int!r}, copies={copies}, image={bool(image_file)}")

    try:
        new_book = Book(
            isbn=isbn_raw,
            title=title_raw,
            author=author_raw,
            publisher=publisher_raw,
            year=year_int,
            copies_total=copies,
            copies_available=copies
        )

        if image_file:
            new_book.image = image_file.read()

        db.session.add(new_book)
        db.session.commit()

        return jsonify({
            'message': 'Book added successfully',
            'book_id': new_book.book_id
        }), 201

    except SQLAlchemyError as db_err:
        # Roll back and log full DB exception
        db.session.rollback()
        tb = traceback.format_exc()
        app.logger.error("SQLAlchemyError adding book:\n" + tb)
        # Return truncated message for clients
        return jsonify({'error': 'A database error occurred'}), 500

    except Exception as e:
        # Catch **everything else**, log the full stacktrace
        tb = traceback.format_exc()
        app.logger.error("Unexpected error adding book:\n" + tb)
        # For debugging you can send the trace back once
        return jsonify({
            'error': str(e),
            'trace': tb.splitlines()[-5:]   # last 5 lines of traceback
        }), 500

@app.route('/books/<int:book_id>/status', methods=['PATCH' , 'OPTIONS'])
def update_book_status(book_id):
    if request.method == 'OPTIONS':
        return '', 200  # allow preflight CORS request
   
    book = Book.query.get(book_id)
    if not book:
        return jsonify({'error': 'Book not found'}), 404
    
    action = request.json.get('action')
    
    if action == 'add':
        book.copies_total += 1
        book.copies_available += 1
    elif action == 'remove':
        if book.copies_available > 0:
            book.copies_available -= 1
        book.copies_total -= 1
    else:
        return jsonify({'error': 'Invalid action'}), 400
    
    db.session.commit()
    return jsonify({
        'copies_total': book.copies_total,
        'copies_available': book.copies_available
    })

@app.route('/books/<int:book_id>', methods=['PUT', 'OPTIONS'])
def update_book(book_id):
    book = Book.query.get(book_id)
    if not book:
        return jsonify({'error': 'Book not found'}), 404

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 200

    # Extract updated fields
    book.title = request.form.get('title', book.title)
    book.author = request.form.get('author', book.author)
    book.publisher = request.form.get('publisher', book.publisher)
    book.year = request.form.get('year', book.year)
    book.isbn = request.form.get('isbn', book.isbn)

    # Optional: update image
    image_file = request.files.get('image')
    if image_file:
        book.image = image_file.read()

    try:
        db.session.commit()
        return jsonify({'message': 'Book updated successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# 4. Create Reservation
@app.route('/books/<int:book_id>/reserve', methods=['POST'])
def reserve_book(book_id):
    book = Book.query.get_or_404(book_id)
    data = request.get_json()
    
    
    if book.copies_available <= 0:
        return jsonify({'error': 'No available copies'}), 400
    
    # Calculate reservation period (default 2 hours)
    reserved_from = datetime.now(timezone.utc)
    reserved_until = reserved_from + timedelta(hours=2)
    
    if 'reserved_until' in data:
        reserved_until = datetime.fromisoformat(data['reserved_until'])
    
    reservation = Reservation(
        user_id = g.current_user.user_id,
        book_id=book_id,
        library_id=data.get('library_id', 1),  
        reserved_from=reserved_from,
        reserved_until=reserved_until
    )
    
    book.copies_available -= 1
    db.session.add(reservation)
    db.session.commit()
    
    return jsonify({
        'reservation_id': reservation.reservation_id,
        'reserved_until': reservation.reserved_until.isoformat()
    }), 201

@app.route('/books/<int:book_id>', methods=['GET'])
def get_book_by_id(book_id):
    book = Book.query.get(book_id)
    if not book:
        return jsonify({'error': 'Book not found'}), 404

    img_b64 = base64.b64encode(book.image).decode('ascii') if book.image else None

    return jsonify({
        'book_id':          book.book_id,
        'isbn':             book.isbn,
        'title':            book.title,
        'author':           book.author,
        'publisher':        book.publisher,
        'year':             book.year,
        'copies_available': book.copies_available,
        'image_base64':     img_b64
    })


# GET /reservations
@app.route('/reservations', methods=['GET', 'OPTIONS'])
def get_reservations():
    user_id = request.args.get('user_id') 
    book_id = request.args.get('book_id', type=int)
    
    query = Reservation.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    if book_id:
        query = query.filter_by(book_id=book_id)
    
    reservations = query.all()
    
    return jsonify({
        'items': [{
            'reservation_id': r.reservation_id,
            'book_id': r.book_id,
            'user_id': r.user_id,
            'reserved_from': r.reserved_from.isoformat(),
            'reserved_until': r.reserved_until.isoformat(),
            'status': r.status
        } for r in reservations]
    })

@app.route('/users/<string:firebase_uid>/reservations', methods=['GET', 'OPTIONS'])
def get_user_reservations(firebase_uid):
    # CORS preflight
    if request.method == 'OPTIONS':
        return '', 200

    # look up your SQL user by firebase_uid
    user = User.query.filter_by(firebase_uid=firebase_uid).first_or_404()

    # optional book filter:
    book_id = request.args.get('book_id', type=int)

    # now filter by the *numeric* user_id
    query = Reservation.query.filter_by(user_id=user.user_id)
    if book_id is not None:
        query = query.filter_by(book_id=book_id)

    items = [{
        'reservation_id': r.reservation_id,
        'book_id':        r.book_id,
        'user_id':        r.user_id,
        'reserved_from':  r.reserved_from.isoformat(),
        'reserved_until': r.reserved_until.isoformat(),
        'status':         r.status,
        
    } for r in query.all()]

    return jsonify({'items': items}), 200


@app.route('/reservations/<int:reservation_id>/collect', methods=['POST'])
def collect_reservation(reservation_id):
    reservation = Reservation.query.get_or_404(reservation_id)
    
    # Verify ownership
    if reservation.user_id != g.current_user.user_id:
        raise Forbidden("You can only collect your own reservations")
    
    # Validate reservation status
    if reservation.status != 'active':
        return jsonify({'error': 'Reservation is not active'}), 400
    
    # Create loan with 5-day default period
    today = date.today()
    loan = Loan(
        user_id=reservation.user_id,
        book_id=reservation.book_id,
        checkout_date=today,
        due_date=today + timedelta(days=5)
    )
    
    # Update reservation status
    reservation.status = 'fulfilled'
    
    # Commit changes
    db.session.add(loan)
    db.session.commit()
    
    return jsonify({
        'loan_id': loan.loan_id,
        'due_date': loan.due_date.isoformat()
    }), 201

@app.route('/reservations/<int:reservation_id>', methods=['DELETE'])
def delete_reservation(reservation_id):
    reservation = Reservation.query.get_or_404(reservation_id)
    book = Book.query.get(reservation.book_id)
    
    # Increase book availability
    book.copies_available += 1
    
    db.session.delete(reservation)
    db.session.commit()
    
    return jsonify({'message': 'Reservation cancelled successfully'}), 200

# GET /loans
@app.route('/loans', methods=['GET'])
def get_loans():
    user_id = request.args.get('user_id', type=int)
    book_id = request.args.get('book_id', type=int)
    
    query = Loan.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    if book_id:
        query = query.filter_by(book_id=book_id)
    
    loans = query.all()
    
    return jsonify({
        'items': [{
            'loan_id': l.loan_id,
            'book_id': l.book_id,
            'user_id': l.user_id,
            'checkout_date': l.checkout_date.isoformat(),
            'due_date':       l.due_date.isoformat() if l.due_date else None, 
            'returned_date': l.returned_date.isoformat() if l.returned_date else None
        } for l in loans]
    })

# fee calculation
def calculate_fees(loan):
   
    if loan.returned_date:
        return 0.0
    today = date.today()
    if today > loan.due_date:
        days_overdue = (today - loan.due_date).days
        return round(days_overdue * 5.00, 2)  # R5 per day
    return 0.0

# PUT /feefine/<int:fee_id>/pay
@app.route('/feefine/<int:fee_id>/pay', methods=['PUT'])
def pay_fee(fee_id):
    fee = FeeFine.query.get_or_404(fee_id)
    
    if fee.status == 'paid':
        return jsonify({'error': 'Fee already paid'}), 400
        
    fee.status = 'paid'
    db.session.commit()
    
    return jsonify({'message': 'Fee paid successfully'}), 200

# 5. Renew Loan
@app.route('/loans/<int:loan_id>/renew', methods=['PUT'])
def renew_loan(loan_id):
    loan = Loan.query.get_or_404(loan_id)
    
    if loan.user_id != request.current_user.user_id:
        raise Forbidden('You can only renew your own loans')
    
    if loan.returned_date:
        return jsonify({'error': 'Book already returned'}), 400
    
    # Renew for 2 weeks
    loan.due_date = loan.due_date + timedelta(weeks=2)
    db.session.commit()
    
    return jsonify({
        'loan_id': loan.loan_id,
        'new_due_date': loan.due_date.isoformat()
    })

# 6. User Fees
@app.route('/users/<string:user_id>/fees', methods=['GET'])
def view_fees(user_id):
    # Assuming g.current_user.user_id is also a Firebase UID string
    if user_id != g.current_user.firebase_uid:
        raise Forbidden('Unauthorized access')
    
    fees = FeeFine.query.filter_by(user_id=user_id, status='unpaid').all()
    total = sum(float(fee.amount) for fee in fees)
    
    return jsonify({
        'total': total,
        'fees': [{
            'id': f.feefine_id,
            'amount': float(f.amount),
            'description': f.description,
            'created_at': f.created_at.isoformat()
        } for f in fees]
    })


# 7. Chat Messages
@app.route('/libraries/<int:library_id>/chat/messages', methods=['GET', 'POST'])
def chat_messages(library_id):
    ref = firebase_db.reference(f'chats/{library_id}/messages')
    
    if request.method == 'GET':
        # Get last 50 messages
        messages = ref.order_by_child('timestamp').limit_to_last(50).get() or {}
        return jsonify(list(messages.values()))
    
    elif request.method == 'POST':
        data = request.get_json()
        msg_id = str(uuid.uuid4())
        
        payload = {
            'user_id': request.current_user.user_id,
            'name': request.current_user.name,
            'text': data['text'],
            'timestamp': datetime.utcnow().isoformat()
        }
        
        ref.child(msg_id).set(payload)
        return jsonify(payload), 201

# 8. Purchase Request
@app.route('/purchase_requests', methods=['POST'])
def create_purchase_request():
    data = request.get_json()
    
    pr = PurchaseRequest(
        user_id=request.current_user.user_id,
        title=data['title'],
        author=data['author'],
        isbn=data.get('isbn'),
        justification=data['justification']
    )
    
    db.session.add(pr)
    db.session.commit()
    
    return jsonify({
        'request_id': pr.request_id,
        'title': pr.title
    }), 201

# 9. Announcements
@app.route('/announcements', methods=['GET'])
def get_announcements():
    active_only = request.args.get('active', 'true') == 'true'
    limit = request.args.get('limit', 5, type=int)
    
    query = Announcement.query.order_by(Announcement.posted_at.desc())
    if active_only:
        query = query.filter_by(is_active=True)
    
    announcements = query.limit(limit).all()
    return jsonify([{
        'id': a.announcement_id,
        'title': a.title,
        'body': a.body,
        'posted_at': a.posted_at.isoformat()
    } for a in announcements])

@app.route('/announcements', methods=['POST'])
def create_announcement():
    data = request.get_json() or {}
    title = data.get('title')
    body  = data.get('body')
    if not title or not body:
        return jsonify({'error': 'Title and body required'}), 400

    ann = Announcement(
        title=title,
        body=body,
        posted_at=datetime.utcnow(),
        is_active=True
    )
    db.session.add(ann)
    db.session.commit()
    return jsonify({
        'id': ann.announcement_id,
        'title': ann.title,
        'body':  ann.body,
        'posted_at': ann.posted_at.isoformat()
    }), 201

# “Soft” delete or fully remove?
@app.route('/announcements/<int:ann_id>', methods=['DELETE'])
def delete_announcement(ann_id):
    ann = Announcement.query.get_or_404(ann_id)
    # Option A: soft‑delete
    # ann.is_active = False
    # db.session.commit()
    # return '', 204

    # Option B: hard‑delete
    db.session.delete(ann)
    db.session.commit()
    return '', 204

# 10. Library Hours
@app.route('/libraries/<int:library_id>/hours', methods=['GET'])
def get_hours(library_id):
    times = OperatingTime.query.filter_by(library_id=library_id).all()
    return jsonify([{
        'weekday': t.weekday,
        'open_time': t.open_time.strftime('%H:%M'),
        'close_time': t.close_time.strftime('%H:%M')
    } for t in times])

@app.route('/libraries/<int:library_id>/hours/<string:weekday>', methods=['PUT'])
def update_hours(library_id, weekday):
    # Validate weekday
    valid_days = ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
    if weekday not in valid_days:
        abort(400, description=f"weekday must be one of {valid_days}")

    data = request.get_json() or {}
    open_str = data.get('open_time')
    close_str = data.get('close_time')
    if not open_str or not close_str:
        abort(400, description="Both 'open_time' and 'close_time' are required in HH:MM format")

    try:
        open_dt = datetime.strptime(open_str, '%H:%M').time()
        close_dt = datetime.strptime(close_str, '%H:%M').time()
    except ValueError:
        abort(400, description="Times must be in 'HH:MM' format")

    # Look for existing record
    entry = OperatingTime.query.filter_by(
        library_id=library_id,
        weekday=weekday
    ).first()

    if entry:
        entry.open_time = open_dt
        entry.close_time = close_dt
    else:
        # Create new if none exists
        entry = OperatingTime(
            library_id=library_id,
            weekday=weekday,
            open_time=open_dt,
            close_time=close_dt
        )
        db.session.add(entry)

    db.session.commit()

    return jsonify({
        'library_id': entry.library_id,
        'weekday': entry.weekday,
        'open_time': entry.open_time.strftime('%H:%M'),
        'close_time': entry.close_time.strftime('%H:%M')
    }), 200


# (Optional) Bulk‐update endpoint if you ever want to send all days at once:
@app.route('/libraries/<int:library_id>/hours', methods=['PUT'])
def bulk_update_hours(library_id):
    payload = request.get_json() or {}
    # payload should be a dict: { "Mon": {open_time:"08:00", close_time:"20:00"}, ... }
    updated = []
    for weekday, times in payload.items():
        if weekday not in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun'):
            continue
        o = times.get('open_time'); c = times.get('close_time')
        if not o or not c:
            continue
        try:
            ot = datetime.strptime(o, '%H:%M').time()
            ct = datetime.strptime(c, '%H:%M').time()
        except ValueError:
            continue

        entry = OperatingTime.query.filter_by(
            library_id=library_id,
            weekday=weekday
        ).first()
        if entry:
            entry.open_time = ot
            entry.close_time = ct
        else:
            entry = OperatingTime(
                library_id=library_id,
                weekday=weekday,
                open_time=ot,
                close_time=ct
            )
            db.session.add(entry)
        updated.append(entry)

    db.session.commit()

    return jsonify([{
        'weekday': e.weekday,
        'open_time': e.open_time.strftime('%H:%M'),
        'close_time': e.close_time.strftime('%H:%M')
    } for e in updated]), 200

# 11. Create Appointment
@app.route('/appointments', methods=['POST'])
def create_appointment():
    data = request.get_json()
    
    # Check librarian exists and is staff
    librarian = User.query.filter_by(
        user_id=data['librarian_user_id'],
        role='staff'
    ).first_or_404()
    
    # Validate time slot
    start = datetime.fromisoformat(data['start_datetime'])
    end = datetime.fromisoformat(data['end_datetime'])
    
    if end <= start:
        return jsonify({'error': 'End time must be after start time'}), 400
    
    # Check for conflicts
    conflict = Appointment.query.filter(
        Appointment.librarian_user_id == librarian.user_id,
        or_(
            and_(Appointment.start_datetime <= start, Appointment.end_datetime > start),
            and_(Appointment.start_datetime < end, Appointment.end_datetime >= end),
            and_(Appointment.start_datetime >= start, Appointment.end_datetime <= end)
        )
    ).first()
    
    if conflict:
        return jsonify({'error': 'Time slot not available'}), 409
    
    appointment = Appointment(
        user_id=request.current_user.user_id,
        librarian_user_id=librarian.user_id,
        library_id=data['library_id'],
        start_datetime=start,
        end_datetime=end,
        notes=data.get('notes', '')
    )
    
    db.session.add(appointment)
    db.session.commit()
    
    return jsonify({
        'appointment_id': appointment.appointment_id,
        'librarian': librarian.name,
        'start': appointment.start_datetime.isoformat()
    }), 201

# 12. Submit Recommendation
@app.route('/recommendations', methods=['POST'])
def submit_recommendation():
    data = request.get_json()
    
    rec = Recommendation(
        user_id=request.current_user.user_id,
        category=data['category'],
        content=data['content']
    )
    
    db.session.add(rec)
    db.session.commit()
    
    return jsonify({'rec_id': rec.rec_id}), 201

# 13. VENUES 
# Get all rooms
@app.route('/libraries/<int:library_id>/rooms', methods=['GET'])
def get_rooms(library_id):
    rooms = Room.query.filter_by(library_id=library_id).all()
    room_list = [{'room_id': r.room_id, 'name': r.name, 'room_type': r.room_type} for r in rooms]
    return jsonify({'rooms': room_list})

# Create new room
@app.route('/libraries/<int:library_id>/rooms', methods=['POST'])
def create_room(library_id):
    data = request.get_json()
    new_room = Room(
        library_id=library_id,
        name=data['name'],
        room_type=data['type']
    )
    db.session.add(new_room)
    db.session.commit()
    return jsonify({
    'room_id': new_room.room_id,
    'name': new_room.name,
    'room_type': new_room.room_type
}), 201

#14 List ALL computers in library ---
@app.route('/libraries/<int:library_id>/computers', methods=['GET'])
def list_computers(library_id):
    comps = (
        Seat.query
        .join(Room)
        .filter(Room.library_id == library_id, Seat.is_computer == True)
        .all()
    )
    return jsonify([{
        'computer_id': s.seat_id,
        'identifier':  s.identifier,
        'specs':       s.specs,
        'is_active':   s.is_active,
        'is_occupied': s.is_occupied,
        'room_id':     s.room_id
    } for s in comps]), 200

# Update a computer’s details (specs, active, occupied) ---
@app.route('/libraries/<int:library_id>/computers/<int:computer_id>', methods=['PUT'])
def update_computer(library_id, computer_id):
    data = request.get_json() or {}

    # 1) fetch and verify belongs to library
    comp = Seat.query.get_or_404(computer_id)
    room = Room.query.filter_by(room_id=comp.room_id, library_id=library_id).first()
    if not room:
        abort(404, description="Computer not found in this library")

    # 2) apply edits
    if 'identifier' in data:
        comp.identifier = data['identifier']
    if 'specs' in data:
        comp.specs = data['specs']
    if 'is_active' in data:
        comp.is_active = bool(data['is_active'])
    if 'is_occupied' in data:
        comp.is_occupied = bool(data['is_occupied'])

    db.session.commit()

    return jsonify({
        'computer_id': comp.seat_id,
        'identifier':  comp.identifier,
        'specs':       comp.specs,
        'is_active':   comp.is_active,
        'is_occupied': comp.is_occupied,
        'room_id':     comp.room_id
    }), 200



# 15. User Registration (Sync with Firebase)
@app.route('/register', methods=['POST'])
def register_user():
    data = request.get_json()
    firebase_uid = data.get('firebase_uid')
    
    if not firebase_uid:
        return jsonify({'error': 'Firebase UID required'}), 400
    
    # Check if user exists
    existing = User.query.filter_by(firebase_uid=firebase_uid).first()
    if existing:
        return jsonify({'user_id': existing.user_id}), 200
    
    # Create new user
    user = User(
        firebase_uid=firebase_uid,
        name=data['name'],
        email=data['email'],
        role=data.get('role', 'student')
    )
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({
        'user_id': user.user_id,
        'name': user.name,
        'email': user.email
    }), 201


@app.route('/users/<string:user_id>/summary', methods=['GET'])
def user_summary(user_id):
    # make sure they’re only looking at their own data
    if user_id != g.current_user.firebase_uid and g.current_user.role != 'staff':
        raise Forbidden('Unauthorized access')

    # Count active reservations & loans
    res_count = Reservation.query \
                 .filter_by(user_id=g.current_user.user_id, status='active') \
                 .count()
    loan_count = Loan.query \
                   .filter_by(user_id=g.current_user.user_id, returned_date=None) \
                   .count()
    # Sum unpaid fees
    total_fees = db.session.query(func.coalesce(func.sum(FeeFine.amount), 0)) \
                   .filter_by(user_id=g.current_user.user_id, status='unpaid') \
                   .scalar()

    return jsonify({
      'reservations': res_count,
      'loans':        loan_count,
      'fees':         float(total_fees)
    })

@app.route('/libraries', methods=['GET'])
def all_libraries():
    libs = Library.query.all()
    return jsonify([{
        'library_id': l.library_id,
        'name':       l.name,
        'location':   l.location,
        'type':       l.type,
    } for l in libs])




# 7. Chat Messages
@app.route('/libraries/<int:library_id>/chat/messages', methods=['GET', 'POST'])
def chat_messages(library_id):
    ref = firebase_db.reference(f'chats/{library_id}/messages')
    
    if request.method == 'GET':
        # Get last 50 messages
        messages = ref.order_by_child('timestamp').limit_to_last(50).get() or {}
        return jsonify(list(messages.values()))
    
    elif request.method == 'POST':
        data = request.get_json()
        msg_id = str(uuid.uuid4())
        
        payload = {
            'user_id': request.current_user.user_id,
            'name': request.current_user.name,
            'text': data['text'],
            'timestamp': datetime.utcnow().isoformat()
        }
        
        ref.child(msg_id).set(payload)
        return jsonify(payload), 201

# 15. User Registration (Sync with Firebase)
@app.route('/register', methods=['POST'])
def register_user():
    data = request.get_json()
    firebase_uid = data.get('firebase_uid')
    
    if not firebase_uid:
        return jsonify({'error': 'Firebase UID required'}), 400
    
    # Check if user exists
    existing = User.query.filter_by(firebase_uid=firebase_uid).first()
    if existing:
        return jsonify({'user_id': existing.user_id}), 200
    
    # Create new user
    user = User(
        firebase_uid=firebase_uid,
        name=data['name'],
        email=data['email'],
        role=data.get('role', 'student')
    )
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({
        'user_id': user.user_id,
        'name': user.name,
        'email': user.email
    }), 201

# --- Study Room Endpoints ---

# Create study room
@app.route('/study_rooms', methods=['POST'])
def create_study_room():
    data = request.get_json()
    new_room = StudyRoom(
        name=data['name'],
        description=data['description'],
        subject=data['subject'],
        capacity=data['capacity'],
        created_by=g.current_user.user_id
    )
    db.session.add(new_room)
    db.session.flush()   # so new_room.room_id is populated

    # Auto‑approve the creator:
    owner_membership = StudyRoomMember(
        room_id=new_room.room_id,
        user_id=g.current_user.user_id,
        student_number=None,      # optional
        student_email=None,       # optional
        status='approved',
        joined_at=datetime.utcnow()
    )
    db.session.add(owner_membership)

    db.session.commit()
    return jsonify({
        'room_id': new_room.room_id,
        'name': new_room.name,
        'created_at': new_room.created_at.isoformat()
    }), 201


# List study rooms
@app.route('/study_rooms', methods=['GET'])
def list_study_rooms():
    rooms = StudyRoom.query.filter_by(is_active=True).all()
    return jsonify([{
        'room_id': r.room_id,
        'name': r.name,
        'description': r.description,
        'subject': r.subject,
        'capacity': r.capacity,
        'created_by': r.created_by,
        'created_at': r.created_at.isoformat(),
        'member_count': StudyRoomMember.query.filter_by(room_id=r.room_id, status='approved').count()
    } for r in rooms])


# Join request with university details
@app.route('/study_rooms/<int:room_id>/join', methods=['POST'])
def request_join_room(room_id):
    data = request.get_json() or {}

    # allow either snake_case or camelCase
    student_number = data.get('student_number') or data.get('studentNumber')
    student_email  = data.get('student_email')  or data.get('studentEmail')

      # DEBUG: show exactly what arrived
    print("💾 /join payload keys:", list(data.keys()))
    student_number = data.get('student_number') or data.get('studentNumber')
    student_email  = data.get('student_email')  or data.get('studentEmail')
    print("💾 student_number:", repr(student_number))
    print("💾 student_email: ", repr(student_email))

    if not student_number or not student_email:
        return jsonify({
            'error': 'Both student_number (or studentNumber) and student_email (or studentEmail) are required'
        }), 400



    room = StudyRoom.query.get_or_404(room_id)
    existing = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id
    ).first()
    if existing:
        return jsonify({'message': 'Join request already exists'}), 400

    new_request = StudyRoomMember(
        room_id=room_id,
        user_id=g.current_user.user_id,
        student_number=student_number,
        student_email=student_email,
        status='pending'
    )
    db.session.add(new_request)
    db.session.commit()
    return jsonify({'message': 'Join request submitted'}), 201

@app.route('/study_rooms/<int:room_id>', methods=['GET'])
def get_study_room(room_id):
    room = StudyRoom.query.get_or_404(room_id)
    
    # Check if current user is approved member
    membership = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first()
    
    if not membership:
        abort(403, description="You must be an approved member to access this room")
    
    return jsonify({
        'room_id': room.room_id,
        'name': room.name,
        'description': room.description,
        'subject': room.subject,
        'capacity': room.capacity,
        'created_by': room.created_by,
        'created_at': room.created_at.isoformat(),
        'is_creator': room.created_by == g.current_user.user_id
    })


# list_pending_requests endpoint
@app.route('/study_rooms/<int:room_id>/members/pending', methods=['GET'])
def list_pending_requests(room_id):
    # Verify room owner
    room = StudyRoom.query.filter_by(
        room_id=room_id,
        created_by=g.current_user.user_id
    ).first()
    
    if not room:
        abort(404, description="Room not found or you're not the creator")
    
    # Get pending requests
    pending = StudyRoomMember.query.filter_by(
        room_id=room_id,
        status='pending'
    ).all()
    
    return jsonify([{
        'user_id': m.user_id,
        'name': m.user.name,
        'student_number': m.student_number,
        'student_email': m.student_email,
        'joined_at': m.joined_at.isoformat() if m.joined_at else None
    } for m in pending])

# List room members
@app.route('/study_rooms/<int:room_id>/members', methods=['GET'])
def list_room_members(room_id):
    # Verify user is approved member
    membership = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first()
    if not membership:
        abort(403, description="You must be an approved member to view members")
    
    members = StudyRoomMember.query.filter_by(room_id=room_id, status='approved').all()
    return jsonify([{
        'user_id': m.user_id,
        'name': m.user.name,
        'student_number': m.student_number,
        'student_email': m.student_email,
        'joined_at': m.joined_at.isoformat() if m.joined_at else None
    } for m in members])

# Approve/reject members
@app.route('/study_rooms/<int:room_id>/members/<int:user_id>', methods=['PUT'])
def update_member_status(room_id, user_id):
    # Verify room owner
    room = StudyRoom.query.filter_by(
        room_id=room_id,
        created_by=g.current_user.user_id
    ).first_or_404()
    
    member = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=user_id
    ).first_or_404()
    
    data = request.get_json()
    member.status = data['status']
    if data['status'] == 'approved':
        member.joined_at = datetime.utcnow()
        
    db.session.commit()
    return jsonify({'message': 'Member status updated'})

@app.route('/study_rooms/<int:room_id>/membership', methods=['GET'])
def get_membership_status(room_id):

    """
    Returns the current user’s membership info for this room:
      - status: 'pending', 'approved', 'rejected', or 'not_member'
      - user_id:    so the front‑end can test `room.created_by === user_id`
      - student_number & student_email: for display if approved
    """
    m = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id
    ).first()

    if not m:
        return jsonify({'status': 'not_member'}), 200

    return jsonify({
        'user_id':        m.user_id,
        'status':         m.status,
        'student_number': m.student_number,
        'student_email':  m.student_email
    }), 200



# Upload media to room
UPLOAD_FOLDER = 'uploads/study_rooms'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'jpg', 'png', 'mp4', 'mov', 'txt'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# media upload 
@app.route('/media/<filename>')
def serve_media(filename):
    return send_from_directory(app.config['MEDIA_UPLOAD_FOLDER'], filename)
@app.route('/study_rooms/<int:room_id>/media', methods=['POST'])
def upload_media(room_id):
    # 1. Verify user is an approved member (your existing logic)
    membership = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first_or_404()

    # 2. Get file from form-data
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # 3. Generate a UUID filename + keep extension
    ext      = os.path.splitext(file.filename)[1]
    uid_name = f"{uuid.uuid4().hex}{ext}"
    safe_name= secure_filename(uid_name)

    # 4. Save to disk
    save_path = os.path.join(app.config['MEDIA_UPLOAD_FOLDER'], safe_name)
    file.save(save_path)

    # 5. Persist in DB
    media = StudyRoomMedia(
        room_id=   room_id,
        user_id=   g.current_user.user_id,
        file_name= safe_name,
        file_type= file.mimetype,
        file_path= save_path
    )
    db.session.add(media)
    db.session.commit()

    # 6. Return the new record (including a URL)
    return jsonify({
        'media_id':   media.media_id,
        'file_name':  media.file_name,
        'file_type':  media.file_type,
        'uploaded_at': media.uploaded_at.isoformat(),
        'url':        url_for('serve_media', filename=media.file_name, _external=True)
    }), 201

# List room media
@app.route('/study_rooms/<int:room_id>/media', methods=['GET'])
def list_room_media(room_id):
    # Verify user is approved member
    membership = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first_or_404()
    
    media_list = StudyRoomMedia.query.filter_by(room_id=room_id).all()
    return jsonify([{
        'media_id': m.media_id,
        'file_name': m.file_name,
        'file_type': m.file_type,
        'uploaded_at': m.uploaded_at.isoformat(),
        'user_id': m.user_id,
        'user_name': m.user.name
    } for m in media_list])

# Download media
@app.route('/media/<int:media_id>', methods=['GET'])
def download_media(media_id):
    media = StudyRoomMedia.query.get_or_404(media_id)
    
    # Verify user is approved member of the room
    membership = StudyRoomMember.query.filter_by(
        room_id=media.room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first()
    if not membership:
        abort(403, description="You are not authorized to download this file")
    
    return send_file(media.file_path, as_attachment=True)


# To do list endpoint 
@app.route('/study_rooms/<int:room_id>/mindmap', methods=['GET', 'POST'])
def room_mindmap(room_id):
    # Check user is approved member
    membership = StudyRoomMember.query.filter_by(
        room_id=room_id,
        user_id=g.current_user.user_id,
        status='approved'
    ).first()
    if not membership:
        abort(403, description="You must be an approved member to access this mindmap")
    
    if request.method == 'GET':
        mindmap = StudyRoomMindMap.query.filter_by(room_id=room_id).first()
        if mindmap:
            return jsonify(mindmap.data)
        return jsonify({'nodes': [], 'connections': []}), 200
    
    elif request.method == 'POST':
        data = request.get_json()
        mindmap = StudyRoomMindMap.query.filter_by(room_id=room_id).first()
        
        if mindmap:
            mindmap.data = data
        else:
            mindmap = StudyRoomMindMap(room_id=room_id, data=data)
            db.session.add(mindmap)
        
        db.session.commit()
        return jsonify({'message': 'Mindmap saved'}), 200


# Error Handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({'error': 'Authentication required'}), 401

@app.errorhandler(403)
def forbidden(error):
    return jsonify({'error': 'Forbidden'}), 403

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'error': 'Bad request'}), 400

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        initialize_library(library_id=1)
    app.run(host='0.0.0.0', port=5003, debug=True)

