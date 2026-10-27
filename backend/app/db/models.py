"""Persistence - SQLAlchemy models. SQLite to start; the layer stays swappable.
Portfolios are persistent and independent; every agent run and every order is
audit-logged with its skill reference and model.
"""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(String, primary_key=True)
    initial_capital = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    realized_pnl = Column(Float, default=0.0)
    created = Column(DateTime, default=utcnow)


class PositionRow(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(String, index=True)
    symbol = Column(String, index=True)
    qty = Column(Float, default=0.0)
    avg_price = Column(Float, default=0.0)


class OrderRow(Base):
    __tablename__ = "orders"
    order_id = Column(String, primary_key=True)
    portfolio_id = Column(String, index=True)
    symbol = Column(String)
    side = Column(String)
    qty = Column(Float)
    type = Column(String)
    limit_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    status = Column(String, default="new")
    filled_price = Column(Float, nullable=True)
    fees = Column(Float, default=0.0)
    note = Column(Text, default="")
    created = Column(DateTime, default=utcnow)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent = Column(String, index=True)
    skill = Column(String)
    skill_ref = Column(String)
    model = Column(String, nullable=True)
    instrument = Column(String, nullable=True)
    status = Column(String)
    duration_ms = Column(Integer)
    result_json = Column(Text)
    errors_json = Column(Text, default="[]")
    ts = Column(DateTime, default=utcnow, index=True)


class ProposalRow(Base):
    __tablename__ = "proposals"
    proposal_id = Column(String, primary_key=True)
    symbol = Column(String)
    order_json = Column(Text)
    risk_json = Column(Text)
    state = Column(String, default="pending")
    ts = Column(DateTime, default=utcnow)


class AlertRow(Base):
    __tablename__ = "alerts_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String)
    detail = Column(Text, default="")
    ts = Column(DateTime, default=utcnow)


def make_engine(url="sqlite:///./trading_opus.db"):
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return engine


def make_session(engine):
    return sessionmaker(bind=engine, future=True)()