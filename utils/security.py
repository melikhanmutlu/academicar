from functools import wraps
from flask import abort
from flask_login import current_user
from models import db, Paper, Model3D

def require_paper_ownership(func):
    """
    Decorator to ensure the current_user owns the paper specified by 'slug' or 'paper_id'.
    Must be used AFTER @login_required.
    """
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if "slug" in kwargs:
            paper = Paper.query.filter_by(slug=kwargs["slug"]).first_or_404()
            if paper.user_id != current_user.id:
                abort(403)
        elif "paper_id" in kwargs:
            paper = db.session.get(Paper, kwargs["paper_id"])
            if not paper:
                abort(404)
            if paper.user_id != current_user.id:
                abort(403)
        else:
            # If the route doesn't have slug or paper_id, we can't check it here.
            pass
        return func(*args, **kwargs)
    return decorated_function

def require_model_ownership(func):
    """
    Decorator to ensure the current_user owns the model specified by 'model_id'.
    Must be used AFTER @login_required.
    """
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if "model_id" in kwargs:
            model = db.session.get(Model3D, kwargs["model_id"])
            if not model:
                abort(404)
            if model.user_id != current_user.id:
                abort(403)
        return func(*args, **kwargs)
    return decorated_function
