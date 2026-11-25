from sqlalchemy import Column, Integer, String, func
from src.models import Base, session

class Clients(Base):
    __tablename__ = 'clients'

    id = Column(Integer, primary_key=True)
    name = Column(String(250), nullable=False)
    document = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), nullable=False)
    address = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    active = Column(Integer, default=1)

    def __init__(self, name, document, email, address, phone, active=1):
        self.name = name
        self.document = document
        self.email = email
        self.address = address
        self.phone = phone
        self.active = active

    def __rep__(self):
        return f"<Client(id={self.id}, name={self.name}, document={self.document}, email={self.email}, active={self.active})>"

    def get_paginated(page, per_page):
        total_clients = session.query(func.count(Clients.id)).scalar()
        clients = session.query(Clients).offset((page - 1) * per_page).limit(per_page).all()
        return clients, total_clients

    def get_by_id(client_id):
        client = session.query(Clients).filter_by(id=client_id).first()
        return client
        
    def get_by_email(email):
        client = session.query(Clients).filter_by(email=email).first()
        return client

    def get_by_name(name):
        client = session.query(Clients).filter_by(name=name).first()
        return client
    
    def get_by_document(document):
        client = session.query(Clients).filter_by(document=document).first()
        return client      

    def save(self):
        session.add(self)
        session.commit()
        
    def update(self):
        session.commit()