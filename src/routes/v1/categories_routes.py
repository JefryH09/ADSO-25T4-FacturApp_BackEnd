from flask import Blueprint, jsonify, request
from sqlalchemy import func
from src.models import session
from src.models.categories import Categories
from src.models.users import Role
from src.utils.auth import token_required, role_required

categories_bp = Blueprint('categories_v1', __name__, url_prefix='/api/v1')


# ============================================================================
# GET /api/v1/categories (Listar con paginación) -> ADMIN o SELLER
# ============================================================================
@categories_bp.route('/categories', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_categories():
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=10, type=int)

    total_categories = session.query(func.count(Categories.id)).scalar()
    categories = (
        session.query(Categories)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    total_pages = (total_categories + per_page - 1) // per_page if per_page > 0 else 1

    return jsonify({
        'data': [
            {
                'id': cat.id,
                'name': cat.name,
                'description': cat.description,
                'active': cat.active
            } for cat in categories
        ],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'total_categories': total_categories,
            'has_next': page < total_pages,
            'has_prev': page > 1
        }
    }), 200


# ============================================================================
# GET /api/v1/categories/<id> (Obtener una por ID) -> ADMIN o SELLER
# ============================================================================
@categories_bp.route('/categories/<int:category_id>', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_category_by_id(category_id):
    category = session.query(Categories).filter_by(id=category_id).first()
    if not category:
        return jsonify({
            'success': False,
            'message': 'Category not found'
        }), 404

    return jsonify({
        'id': category.id,
        'name': category.name,
        'description': category.description,
        'active': category.active
    }), 200


# ============================================================================
# POST /api/v1/categories (Crear categoría) -> Solo ADMIN
# ============================================================================
@categories_bp.route('/categories', methods=['POST'])
@token_required
@role_required(Role.ADMIN)
def post_categories():
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    description = data.get('description')
    
    errors = {}
    if not name or not str(name).strip():
        errors['name'] = 'name is required'
    else:
        name = str(name).strip()
        existing = session.query(Categories).filter_by(name=name).first()
        if existing:
            errors['name'] = 'category name already exists'

    if errors:
        return jsonify({
            'success': False,
            'message': 'Errors in data validation',
            'validation-errors': errors
        }), 400

    new_category = Categories(name=name, description=description)
    session.add(new_category)
    session.commit()

    return jsonify({
        'id': new_category.id,
        'name': new_category.name,
        'description': new_category.description,
        'active': new_category.active
    }), 201


# ============================================================================
# PUT /api/v1/categories/<id> (Actualizar categoría) -> Solo ADMIN
# ============================================================================
@categories_bp.route('/categories/<int:category_id>', methods=['PUT'])
@token_required
@role_required(Role.ADMIN)
def put_category(category_id):
    category = session.query(Categories).filter_by(id=category_id).first()
    if not category:
        return jsonify({
            'success': False,
            'message': 'Category not found'
        }), 404

    data = request.get_json(silent=True) or {}
    name = data.get('name')
    description = data.get('description')
    active = data.get('active')

    errors = {}
    if name is not None:
        name = str(name).strip()
        if not name:
            errors['name'] = 'name cannot be empty'
        elif name != category.name:
            existing = session.query(Categories).filter_by(name=name).first()
            if existing:
                errors['name'] = 'category name already exists'
        else:
            category.name = name

    if errors:
        return jsonify({
            'success': False,
            'message': 'Errors in data validation',
            'validation-errors': errors
        }), 400

    if name is not None and 'name' not in errors:
        category.name = name
    if description is not None:
        category.description = description
    if active is not None:
        category.active = 1 if active in [1, True, '1'] else 0

    session.commit()

    return jsonify({
        'id': category.id,
        'name': category.name,
        'description': category.description,
        'active': category.active
    }), 200


