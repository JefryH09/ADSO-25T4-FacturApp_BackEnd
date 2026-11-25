from datetime import datetime
from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, String
from sqlalchemy.orm import relationship
from src.models import Base, session


class Invoices(Base):
    __tablename__ = 'invoices'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)  # El vendedor que emite
    date = Column(DateTime, default=datetime.utcnow)
    total = Column(Float, nullable=False, default=0.0)
    active = Column(Integer, default=1)
    status = Column(String(20), default='OPEN')

    # Relación con el detalle de la factura
    details = relationship('InvoiceDetails', backref='invoice', lazy=True)

    def __init__(self, client_id, user_id, total=0.0, active=1, status='OPEN'):
        self.client_id = client_id
        self.user_id = user_id
        self.total = total
        self.active = active
        self.status = status

    def save(self):
        session.add(self)
        session.commit()


class InvoiceDetails(Base):
    __tablename__ = 'invoice_details'

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey('invoices.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    subtotal = Column(Float, nullable=False)

    def __init__(self, invoice_id, product_id, quantity, unit_price, subtotal):
        self.invoice_id = invoice_id
        self.product_id = product_id
        self.quantity = quantity
        self.unit_price = unit_price
        self.subtotal = subtotal