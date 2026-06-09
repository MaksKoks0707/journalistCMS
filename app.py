import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt
from models import db, User, Article, Comment, UserRole, ArticleStatus
from tasks import make_celery, check_for_scheduled_articles
from functools import wraps

app = Flask(__name__)

CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'postgresql://postgres:admin@localhost:5432/cms_db')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'jhasgkdvuk1238o6t6AGAy67LOIK')
app.config['CELERY_BROKER_URL'] = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')

db.init_app(app)
jwt = JWTManager(app)
celery = make_celery(app)

@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(60.0, check_for_scheduled_articles)

def role_required(required_role):
    def decorator(f):
        @wraps(f)
        @jwt_required()
        def wrapper(*args, **kwargs):
            claims = get_jwt()
            if claims.get("role") != required_role.value:
                return jsonify({"msg": "Brak uprawnień"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    user = User(username=data['username'], role=UserRole(data['role']))
    user.set_password(data['password'])
    db.session.add(user)
    db.session.commit()
    return jsonify({"msg": "Zarejestrowano"}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user and user.check_password(data['password']):
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role.value})
        return jsonify(access_token=token)
    return jsonify({"msg": "Błąd logowania"}), 401

@app.route('/articles', methods=['POST'])
@role_required(UserRole.JOURNALIST)
def create_article():
    data = request.json
    new_art = Article(
        title=data['title'],
        content=data['content'],
        author_id=int(get_jwt_identity()),
        status=ArticleStatus.DRAFT
    )
    db.session.add(new_art)
    db.session.commit()
    return jsonify({"id": new_art.id, "status": "draft"}), 201

@app.route('/articles/my', methods=['GET'])
@role_required(UserRole.JOURNALIST)
def list_my_articles():
    current_user_id = int(get_jwt_identity())
    articles = Article.query.filter_by(author_id=current_user_id).all()
    return jsonify([
        {"id": a.id, "title": a.title, "content": a.content, "status": a.status.value, "scheduled_at": a.scheduled_at} 
        for a in articles
    ])

@app.route('/articles/<int:id>', methods=['PUT'])
@role_required(UserRole.JOURNALIST)
def update_article(id):
    art = Article.query.get_or_404(id)
    if art.author_id != int(get_jwt_identity()):
        return jsonify({"msg": "To nie Twój artykuł"}), 403
    
    data = request.json
    if 'title' in data:
        art.title = data['title']
    if 'content' in data:
        art.content = data['content']
        
    db.session.commit()
    return jsonify({"msg": "Artykuł zaktualizowany", "id": art.id})

@app.route('/articles/<int:id>', methods=['DELETE'])
@role_required(UserRole.JOURNALIST)
def delete_article(id):
    art = Article.query.get_or_404(id)
    if art.author_id != int(get_jwt_identity()):
        return jsonify({"msg": "To nie Twój artykuł"}), 403
        
    db.session.delete(art)
    db.session.commit()
    return jsonify({"msg": "Artykuł usunięty"})

@app.route('/articles/<int:id>/submit', methods=['POST'])
@role_required(UserRole.JOURNALIST)
def submit_for_review(id):
    art = Article.query.get_or_404(id)
    if art.author_id != int(get_jwt_identity()):
        return jsonify({"msg": "To nie Twój artykuł"}), 403
    art.status = ArticleStatus.PENDING
    db.session.commit()
    return jsonify({"msg": "Wysłano do moderacji"})

@app.route('/articles/pending', methods=['GET'])
@role_required(UserRole.MODERATOR)
def list_pending_articles():
    articles = Article.query.filter_by(status=ArticleStatus.PENDING).all()
    return jsonify([
        {"id": a.id, "title": a.title, "content": a.content, "author_id": a.author_id} 
        for a in articles
    ])

@app.route('/articles/<int:id>/approve', methods=['POST'])
@role_required(UserRole.MODERATOR)
def approve_article(id):
    art = Article.query.get_or_404(id)
    data = request.json

    if data and data.get('scheduled_at'):
        art.status = ArticleStatus.SCHEDULED
        art.scheduled_at = data['scheduled_at']
    else:
        art.status = ArticleStatus.PUBLISHED

    db.session.commit()
    return jsonify({"msg": "Zatwierdzono"})

@app.route('/articles', methods=['GET'])
def list_articles():
    articles = Article.query.filter_by(status=ArticleStatus.PUBLISHED).all()
    return jsonify([{"id": a.id, "title": a.title, "content": a.content} for a in articles])

@app.route('/articles/<int:id>', methods=['GET'])
def get_article(id):
    art = Article.query.get_or_404(id)
    return jsonify({
        "id": art.id, 
        "title": art.title, 
        "content": art.content, 
        "author_id": art.author_id,
        "status": art.status.value
    })

@app.route('/articles/<int:id>/comments', methods=['GET'])
def list_comments(id):
    comments = Comment.query.filter_by(article_id=id).all()
    return jsonify([
        {"id": c.id, "content": c.content, "user_id": c.user_id} 
        for c in comments
    ])

@app.route('/articles/<int:id>/comments', methods=['POST'])
@role_required(UserRole.READER)
def add_comment(id):
    data = request.json
    comment = Comment(content=data['content'], article_id=id, user_id=get_jwt_identity())
    db.session.add(comment)
    db.session.commit()
    return jsonify({"msg": "Dodano komentarz, czeka na moderację"})
    
@app.route('/comments/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_comment(id):
    comment = Comment.query.get_or_404(id)
    current_user_id = int(get_jwt_identity())
    claims = get_jwt()
    
    if comment.user_id != current_user_id and claims.get("role") != UserRole.MODERATOR.value:
        return jsonify({"msg": "Brak uprawnień"}), 403
        
    db.session.delete(comment)
    db.session.commit()
    return jsonify({"msg": "Komentarz usunięty"})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
