from flask import Blueprint, jsonify, request, g
from src.models import session
from src.models.invoices import Invoices, InvoiceDetails
from src.models.clients import Clients
from src.models.products import Products
from src.models.users import Users, Role
from src.utils.auth import token_required, role_required

invoices_bp = Blueprint('invoices_v1', __name__, url_prefix='/api/v1')


# ============================================================================
# 1. CREAR FACTURA -> ADMIN y SELLER
# ============================================================================
@invoices_bp.route('/invoices', methods=['POST'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def create_invoice():
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    items = data.get('items', [])

    errors = {}

    # 1. Determinar el vendedor (user_id)
    if g.current_user.role == Role.ADMIN.value:
        # El ADMIN puede asignar la factura a otro vendedor, o a sí mismo si no envía user_id
        assigned_user_id = data.get('user_id', g.current_user.id)
        seller = Users.get_by_id(assigned_user_id)
        if not seller:
            errors['user_id'] = 'Assigned seller/user does not exist'
        else:
            invoice_user_id = seller.id
    else:
        # El SELLER siempre factura con su propio ID
        invoice_user_id = g.current_user.id

    # 2. Validar que el cliente exista y esté ACTIVO
    if not client_id:
        errors['client_id'] = 'client_id is required'
    else:
        client = Clients.get_by_id(client_id)
        if not client:
            errors['client_id'] = 'Client not found'
        elif client.active != 1:
            errors['client_id'] = 'Cannot create invoice for an inactive client'

    # 3. Validar lista de productos
    if not items or not isinstance(items, list):
        errors['items'] = 'items list is required and cannot be empty'

    if errors:
        return jsonify({'success': False, 'message': 'Errors in data validation', 'validation-errors': errors}), 400

    # 4. Validar existencias de stock y calcular totales (en memoria antes de persistir)
    total_invoice = 0.0
    processed_items = []

    for idx, item in enumerate(items):
        prod_id = item.get('product_id')
        qty = item.get('quantity')

        if not prod_id or not qty or float(qty) <= 0:
            errors[f'item_{idx}'] = 'product_id and a positive quantity are required'
            continue

        product = Products.get_by_id(prod_id)
        if not product:
            errors[f'item_{idx}'] = f'Product with id {prod_id} not found'
            continue

        if product.active != 1:
            errors[f'item_{idx}'] = f"Product '{product.name}' is inactive"
            continue

        if product.stock < float(qty):
            errors[f'item_{idx}'] = f"Insufficient stock for '{product.name}'. Available: {product.stock}, requested: {qty}"
            continue

        subtotal = product.price * float(qty)
        total_invoice += subtotal

        processed_items.append({
            'product': product,
            'quantity': float(qty),
            'unit_price': product.price,
            'subtotal': subtotal
        })

    if errors:
        return jsonify({'success': False, 'message': 'Errors in items validation', 'validation-errors': errors}), 400

    # 5. Persistencia Atómica: Crear factura, detalles y descontar stock
    try:
        new_invoice = Invoices(
            client_id=client_id,
            user_id=invoice_user_id,
            total=total_invoice,
            active=1,
            status='OPEN'
        )
        session.add(new_invoice)
        session.flush()  # Obtiene el new_invoice.id antes del commit

        for p_item in processed_items:
            detail = InvoiceDetails(
                invoice_id=new_invoice.id,
                product_id=p_item['product'].id,
                quantity=p_item['quantity'],
                unit_price=p_item['unit_price'],
                subtotal=p_item['subtotal']
            )
            session.add(detail)

            # Descontar stock del producto
            p_item['product'].stock -= p_item['quantity']

        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Error saving invoice: {str(e)}'}), 500

    return jsonify({
        'id': new_invoice.id,
        'client_id': new_invoice.client_id,
        'user_id': new_invoice.user_id,
        'date': new_invoice.date.strftime('%Y-%m-%d %H:%M:%S'),
        'total': new_invoice.total,
        'items_count': len(processed_items)
    }), 201


# ============================================================================
# 2. LISTAR FACTURAS -> ADMIN (todas) y SELLER (solo las suyas)
# ============================================================================
@invoices_bp.route('/invoices', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_invoices():
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=3, type=int)

    query = session.query(Invoices)

    # REGLA RBAC: Si es SELLER, solo ve sus propias ventas
    if g.current_user.role == Role.SELLER.value:
        query = query.filter_by(user_id=g.current_user.id)

    total_invoices = query.count()
    invoices = query.order_by(Invoices.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total_invoices + per_page - 1) // per_page if per_page > 0 else 1

    return jsonify({
        'data': [{
            'id': inv.id,
            'client_id': inv.client_id,
            'user_id': inv.user_id,
            'date': inv.date.strftime('%Y-%m-%d %H:%M:%S'),
            'total': inv.total,
            'active': inv.active
        } for inv in invoices],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total_items': total_invoices,
            'total_pages': total_pages
        }
    }), 200


# ============================================================================
# 3. VER DETALLE DE UNA FACTURA -> ADMIN o el SELLER dueño de la venta
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>', methods=['GET'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def get_invoice_by_id(invoice_id):
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    # Si es SELLER, solo puede consultar sus propias facturas
    if g.current_user.role == Role.SELLER.value and invoice.user_id != g.current_user.id:
        return jsonify({'success': False, 'message': 'You do not have permission to view this invoice'}), 403

    client = Clients.get_by_id(invoice.client_id)

    # Detalle con nombre del producto y subtotales
    items_detail = []
    for d in invoice.details:
        prod = Products.get_by_id(d.product_id)
        items_detail.append({
            'product_id': d.product_id,
            'product_name': prod.name if prod else 'Unknown',
            'unit_price': d.unit_price,
            'quantity': d.quantity,
            'subtotal': d.subtotal
        })

    return jsonify({
        'id': invoice.id,
        'date': invoice.date.strftime('%Y-%m-%d %H:%M:%S'),
        'seller_id': invoice.user_id,
        'client': {
            'id': client.id if client else None,
            'name': client.name if client else 'Unknown',
            'document': client.document if client else None
        },
        'items': items_detail,
        'total': invoice.total,
        'active': invoice.active
    }), 200

# ============================================================================
# 4. AGREGAR PRODUCTO A FACTURA ABIERTA -> Solo SELLER dueño (o ADMIN)
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>/items', methods=['POST'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def add_item_to_invoice(invoice_id):
    # 1. Buscar la factura
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    # 2. Validar que la factura esté abierta
    
    # Si la factura ya se completó (active == 2) o se canceló (active == 0), se rechaza
    if invoice.active == 2:
        return jsonify({'success': False, 'message': 'Cannot modify a completed invoice'}), 400

    if invoice.active == 0:
        return jsonify({'success': False, 'message': 'Cannot modify a cancelled invoice'}), 400

    if invoice.active != 1:
            return jsonify({'success': False, 'message': 'Cannot add items to a closed or inactive invoice'}), 400
    

    # 3. Regla de seguridad RBAC: Si es SELLER, debe ser el dueño de la factura
    if g.current_user.role == Role.SELLER.value and invoice.user_id != g.current_user.id:
        return jsonify({'success': False, 'message': 'You do not have permission to modify this invoice'}), 403

    # 4. Validar datos del producto a agregar
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    quantity = data.get('quantity')

    errors = {}
    if not product_id:
        errors['product_id'] = 'product_id is required'
    if not quantity:
        errors['quantity'] = 'quantity is required'
    else:
        try:
            quantity = float(quantity)
            if quantity <= 0:
                errors['quantity'] = 'quantity must be greater than 0'
        except (ValueError, TypeError):
            errors['quantity'] = 'quantity must be a valid number'

    if errors:
        return jsonify({'success': False, 'message': 'Errors in data validation', 'validation-errors': errors}), 400

    # 5. Validar existencia y stock del producto
    product = Products.get_by_id(product_id)
    if not product:
        return jsonify({'success': False, 'message': f'Product with id {product_id} not found'}), 404

    if product.active != 1:
        return jsonify({'success': False, 'message': f"Product '{product.name}' is inactive"}), 400

    if product.stock < quantity:
        return jsonify({
            'success': False,
            'message': f"Insufficient stock for '{product.name}'. Available: {product.stock}, requested: {quantity}"
        }), 400

    # 6. Operación Atómica en Base de Datos
    try:
        subtotal = product.price * quantity

        # Si el producto ya estaba en la factura, sumamos la cantidad y el subtotal
        existing_detail = session.query(InvoiceDetails).filter_by(
            invoice_id=invoice.id,
            product_id=product.id
        ).first()

        if existing_detail:
            existing_detail.quantity += quantity
            existing_detail.subtotal += subtotal
        else:
            # Si es nuevo en la factura, creamos la línea de detalle
            new_detail = InvoiceDetails(
                invoice_id=invoice.id,
                product_id=product.id,
                quantity=quantity,
                unit_price=product.price,
                subtotal=subtotal
            )
            session.add(new_detail)

        # A. Actualizar el total de la factura
        invoice.total += subtotal

        # B. Descontar el inventario del producto
        product.stock -= quantity

        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Error adding item: {str(e)}'}), 500

    # 7. Retornar la factura actualizada
    return jsonify({
        'success': True,
        'message': f"Product '{product.name}' added successfully",
        'invoice': {
            'id': invoice.id,
            'client_id': invoice.client_id,
            'user_id': invoice.user_id,
            'total': invoice.total,
            'items_count': len(invoice.details)
        }
    }), 200

# ============================================================================
# 5. MODIFICAR CANTIDAD DE UN PRODUCTO -> Solo ADMIN
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>/items/<int:product_id>', methods=['PUT'])
@token_required
@role_required(Role.ADMIN)
def update_invoice_item_quantity(invoice_id, product_id):
    # 1. Validar que la factura exista y esté abierta
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    if invoice.active == 2 or getattr(invoice, 'status', None) == 'COMPLETED':
        return jsonify({'success': False, 'message': 'Cannot modify a completed invoice'}), 400
    
    if invoice.active == 0 or getattr(invoice, 'status', None) == 'CANCELLED':
        return jsonify({'success': False, 'message': 'Cannot modify a cancelled invoice'}), 400
    
    # 2. Validar que el producto esté en esta factura
    detail = session.query(InvoiceDetails).filter_by(
        invoice_id=invoice.id,
        product_id=product_id
    ).first()
    if not detail:
        return jsonify({'success': False, 'message': 'Product is not in this invoice'}), 404

    # 3. Validar la nueva cantidad
    data = request.get_json(silent=True) or {}
    new_quantity = data.get('quantity')
    try:
        new_quantity = float(new_quantity)
        if new_quantity <= 0:
            return jsonify({'success': False, 'message': 'quantity must be greater than 0'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'quantity must be a valid number'}), 400

    product = Products.get_by_id(product_id)

    # 4. Cálculo diferencial del stock:
    # diff > 0 -> El cliente quiere MÁS unidades (descontamos stock)
    # diff < 0 -> El cliente quiere MENOS unidades (devolvemos stock)
    diff = new_quantity - detail.quantity

    if diff > 0 and product.stock < diff:
        return jsonify({
            'success': False,
            'message': f"Insufficient stock. Available to add: {product.stock}, requested: {diff}"
        }), 400

    # 5. Persistencia atómica
    try:
        # Ajustar inventario del producto
        product.stock -= diff

        # Ajustar subtotal y total de la factura
        old_subtotal = detail.subtotal
        detail.quantity = new_quantity
        detail.subtotal = new_quantity * detail.unit_price

        # Actualizar total general
        invoice.total = (invoice.total - old_subtotal) + detail.subtotal

        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'message': 'Item quantity updated successfully',
        'updated_item': {
            'product_id': product.id,
            'product_name': product.name,
            'unit_price': detail.unit_price,
            'old_quantity': new_quantity - diff,
            'new_quantity': detail.quantity,
            'subtotal': detail.subtotal
        },
        'invoice_total': invoice.total
    }), 200


# ============================================================================
# 6. QUITAR UN PRODUCTO DE LA FACTURA -> Solo ADMIN
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>/items/<int:product_id>', methods=['DELETE'])
@token_required
@role_required(Role.ADMIN)
def remove_invoice_item(invoice_id, product_id):
    # 1. Validar que la factura exista y esté abierta
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    if invoice.active == 2 or getattr(invoice, 'status', None) == 'COMPLETED':
        return jsonify({'success': False, 'message': 'Cannot modify a completed invoice'}), 400
    
    if invoice.active == 0 or getattr(invoice, 'status', None) == 'CANCELLED':
        return jsonify({'success': False, 'message': 'Cannot modify a cancelled invoice'}), 400

    # 2. Validar que el producto pertenezca a la factura
    detail = session.query(InvoiceDetails).filter_by(
        invoice_id=invoice.id,
        product_id=product_id
    ).first()
    if not detail:
        return jsonify({'success': False, 'message': 'Product is not in this invoice'}), 404

    product = Products.get_by_id(product_id)

    # 3. Operación Atómica: Revertir stock y eliminar línea
    try:
        # A. Devolver TODO el inventario vendido de este producto
        if product:
            product.stock += detail.quantity

        # B. Restar el subtotal al gran total de la factura
        invoice.total -= detail.subtotal
        if invoice.total < 0:
            invoice.total = 0.0

        # C. Eliminar el registro del detalle
        session.delete(detail)

        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'message': f"Product with id {product_id} removed from invoice and stock was restored",
        'restored_stock': detail.quantity,
        'invoice_total': invoice.total
    }), 200

# ============================================================================
# 7. MARCAR FACTURA COMO COMPLETADA -> SELLER o ADMIN
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>/complete', methods=['PUT'])
@token_required
@role_required(Role.ADMIN, Role.SELLER)
def complete_invoice(invoice_id):
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    # Regla RBAC: Si es SELLER, debe ser su propia factura
    if g.current_user.role == Role.SELLER.value and invoice.user_id != g.current_user.id:
        return jsonify({'success': False, 'message': 'You do not have permission to complete this invoice'}), 403

    # Validaciones de estado
    if invoice.active == 2:
        return jsonify({'success': False, 'message': 'Invoice is already completed'}), 400
    if invoice.active == 0:
        return jsonify({'success': False, 'message': 'Cannot complete a cancelled invoice'}), 400

    # No se puede completar una factura vacía sin productos
    if not invoice.details or len(invoice.details) == 0:
        return jsonify({'success': False, 'message': 'Cannot complete an invoice with no items'}), 400

    try:
        invoice.active = 2  # Estado COMPLETADA
        invoice.status = 'COMPLETE'
        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'message': 'Invoice marked as COMPLETED successfully',
        'invoice': {
            'id': invoice.id,
            'status': 'COMPLETED',
            'client_id': invoice.client_id,
            'seller_id': invoice.user_id,
            'total': invoice.total,
            'items_count': len(invoice.details)
        }
    }), 200

# ============================================================================
# 8. CANCELAR FACTURA Y DEVOLVER STOCK -> Solo ADMIN
# ============================================================================
@invoices_bp.route('/invoices/<int:invoice_id>/cancel', methods=['PUT'])
@token_required
@role_required(Role.ADMIN)
def cancel_invoice(invoice_id):
    invoice = session.query(Invoices).filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404

    if invoice.active == 0:
        return jsonify({'success': False, 'message': 'Invoice is already cancelled'}), 400

    try:
        # 1. Reintegrar el stock de cada producto al inventario
        restored_items = []
        for detail in invoice.details:
            product = Products.get_by_id(detail.product_id)
            if product:
                product.stock += detail.quantity  # Devolución de stock
                restored_items.append({
                    'product_id': product.id,
                    'product_name': product.name,
                    'restored_quantity': detail.quantity,
                    'new_stock': product.stock
                })

        # 2. Marcar la factura como cancelada
        invoice.active = 0  # Estado CANCELADA
        invoice.status = 'CANCELLED'
        session.commit()
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'message': 'Invoice cancelled successfully and all stock was restored to inventory',
        'invoice_id': invoice.id,
        'status': 'CANCELLED',
        'restored_items': restored_items
    }), 200


