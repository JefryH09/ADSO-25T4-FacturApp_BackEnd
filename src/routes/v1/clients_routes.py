from flask import Blueprint, jsonify, request
from src.models import session
from src.models.clients import Clients
from src.models.users import Role
from src.utils.auth import token_required, role_required

clients_bp = Blueprint('clients_v1', __name__, url_prefix='/api/v1')


# ============================================================================
# 1. LISTAR CLIENTES -> ADMIN y SELLER
# Permite filtrar con ?active_only=true para emitir facturas solo a clientes activos
# ============================================================================
@clients_bp.route('/clients', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_clients():
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=3, type=int)
    
    clients, total = Clients.get_paginated(page=page, per_page=per_page)
    total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1

    return jsonify({
        'data': [{
            'id': c.id,
            'document': c.document,
            'name': c.name,
            'email': c.email,
            'phone': c.phone,
            'address': c.address,
            'active': c.active
        } for c in clients],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total_items': total,
            'total_pages': total_pages
        }
    }), 200


# ============================================================================
# 2. OBTENER CLIENTE POR ID -> ADMIN y SELLER
# ============================================================================
@clients_bp.route('/clients/<int:client_id>', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_client_by_id(client_id):
    client = Clients.get_by_id(client_id)
    if not client:
        return jsonify({'success': False, 'message': 'Client not found'}), 404

    return jsonify({
        'id': client.id,
        'document': client.document,
        'name': client.name,
        'email': client.email,
        'phone': client.phone,
        'address': client.address,
        'active': client.active
    }), 200


# ============================================================================
# 3. REGISTRAR CLIENTE -> Solo ADMIN
# ============================================================================
@clients_bp.route('/clients', methods=['POST'])
@token_required
@role_required(Role.ADMIN)
def create_client():
    data = request.get_json(silent=True) or {}
    document = str(data.get('document', '')).strip()
    name = str(data.get('name', '')).strip()
    email = data.get('email')
    phone = data.get('phone')
    address = data.get('address')

    errors = {}
    if not document:
        errors['document'] = 'document is required'
    elif Clients.get_by_document(document):
        errors['document'] = 'document must be unique'

    if not name:
        errors['name'] = 'name is required'

    if errors:
        return jsonify({
            'success': False,
            'message': 'Errors in data validation',
            'validation-errors': errors
        }), 400

    new_client = Clients(
        document=document,
        name=name,
        email=email,
        phone=phone,
        address=address,
        active=1  # Nace activo listo para facturación
    )
    new_client.save()

    return jsonify({
        'id': new_client.id,
        'document': new_client.document,
        'name': new_client.name,
        'email': new_client.email,
        'phone': new_client.phone,
        'address': new_client.address,
        'active': new_client.active
    }), 201


# ============================================================================
# 4. ACTUALIZAR CLIENTE -> Solo ADMIN (Atómico: Validate First, Mutate Later)
# ============================================================================
@clients_bp.route('/clients/<int:client_id>', methods=['PUT'])
@token_required
@role_required(Role.ADMIN)
def update_client(client_id):
    client = Clients.get_by_id(client_id)
    if not client:
        return jsonify({'success': False, 'message': 'Client not found'}), 404

    data = request.get_json(silent=True) or {}
    errors = {}
    validated_data = {}

    # Validación de documento único
    if 'document' in data and data['document'] is not None:
        doc = str(data['document']).strip()
        if not doc:
            errors['document'] = 'document cannot be empty'
        elif doc != client.document:
            existing = Clients.get_by_document(doc)
            if existing and existing.id != client.id:
                errors['document'] = 'document must be unique'
            else:
                validated_data['document'] = doc
        else:
            validated_data['document'] = doc

    # Validación de nombre
    if 'name' in data and data['name'] is not None:
        name = str(data['name']).strip()
        if not name:
            errors['name'] = 'name cannot be empty'
        else:
            validated_data['name'] = name

    # Campos opcionales
    if 'email' in data:
        validated_data['email'] = data['email']
    if 'phone' in data:
        validated_data['phone'] = data['phone']
    if 'address' in data:
        validated_data['address'] = data['address']
    if 'active' in data and data['active'] is not None:
        validated_data['active'] = 1 if data['active'] in [1, True, '1'] else 0

    # Barrera: Si hay error, abortar sin tocar la base de datos
    if errors:
        session.rollback()
        return jsonify({
            'success': False,
            'message': 'Errors in data validation',
            'validation-errors': errors
        }), 400

    # Si todo es válido, aplicar cambios
    for field, val in validated_data.items():
        setattr(client, field, val)

    client.update()

    return jsonify({
        'id': client.id,
        'document': client.document,
        'name': client.name,
        'email': client.email,
        'phone': client.phone,
        'address': client.address,
        'active': client.active
    }), 200