"""v1 router aggregation. Phase 2+ modules mount here with same org-scoped pattern."""
from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.comparison.router import router as comparison_router
from app.modules.contracts.router import router as contracts_router
from app.modules.llm_explanation.router import router as explanations_router
from app.modules.ml_nlp.router import router as classification_router
from app.modules.ml_evaluation.router import router as ml_evaluation_router
from app.modules.notifications.router import router as notifications_router
from app.modules.obligations.router import router as obligations_router
from app.modules.organizations.router import router as orgs_router
from app.modules.policy_engine.router import overview_router as policies_overview_router
from app.modules.policy_engine.router import router as policies_router
from app.modules.portfolio.router import router as portfolio_router
from app.modules.retrieval.router import router as retrieval_router
from app.modules.review.router import router as review_router
from app.modules.risk_engine.router import router as risk_router
from app.modules.search.router import router as search_router
from app.modules.templates.router import router as templates_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(orgs_router)
api_router.include_router(contracts_router)
api_router.include_router(classification_router)
api_router.include_router(ml_evaluation_router)
api_router.include_router(notifications_router)
api_router.include_router(obligations_router)
api_router.include_router(policies_router)
api_router.include_router(policies_overview_router)
api_router.include_router(portfolio_router)
api_router.include_router(retrieval_router)
api_router.include_router(search_router)
api_router.include_router(risk_router)
api_router.include_router(review_router)
api_router.include_router(explanations_router)
api_router.include_router(templates_router)
api_router.include_router(comparison_router)
