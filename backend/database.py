"""
数据库模块 - SQLite + SQLAlchemy
"""
import os
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, Text, Float, DateTime, JSON
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DB_PATH

os.makedirs(DB_PATH.parent, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CompareHistory(Base):
    """对比历史记录"""
    __tablename__ = "compare_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    models = Column(JSON, nullable=False)  # ["deepseek-v4-pro", "qwen-plus", ...]
    results = Column(JSON, nullable=False)  # 每个模型的返回结果
    diff_summary = Column(Text, default="")  # 差异汇总
    created_at = Column(DateTime, default=datetime.now)


class CustomModel(Base):
    """用户自定义模型"""
    __tablename__ = "custom_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    base_url = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    api_key = Column(Text, nullable=False)
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)


Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
