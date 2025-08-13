from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from config import Base

class Estimate(Base):
    __tablename__ = "estimates"
    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, default="draft")  # draft, needs_clarification, final
    total_cost = Column(Float, default=0)
    currency = Column(String, default="USD")
    base_json = Column(JSON)     # first pass from plans
    final_json = Column(JSON)    # latest version after clarifications
    assumptions = Column(JSON)   # list of {topic, assumption, confidence}
    questions = Column(JSON)     # list of questions to ask user
    changes = relationship("EstimateChange", back_populates="estimate")

class EstimateChange(Base):
    __tablename__ = "estimate_changes"
    id = Column(Integer, primary_key=True, index=True)
    estimate_id = Column(Integer, ForeignKey("estimates.id"))
    change_text = Column(Text)  # free text (answers / instructions)
    ai_response = Column(Text)  # model rationale (optional)
    cost_json = Column(JSON)    # new snapshot
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    estimate = relationship("Estimate", back_populates="changes")
