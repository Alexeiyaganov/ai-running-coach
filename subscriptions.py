"""Модуль для управления подписками и квотами."""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from db import (
    get_user_subscription,
    get_usage_count,
    log_usage,
    create_subscription,
    get_plan_by_name,
)

logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Управляет подписками и проверкой квот."""
    
    @staticmethod
    def get_user_plan(user_id: int) -> Dict[str, Any]:
        """Получает текущий план пользователя (или free по умолчанию)."""
        sub = get_user_subscription(user_id)
        if sub:
            return sub
        
        # Возвращаем free plan по умолчанию
        free_plan = get_plan_by_name("free")
        if free_plan:
            return {
                "subscription_id": None,
                "plan_name": "free",
                "plan_display_name": free_plan["display_name"],
                "max_trainings_per_month": free_plan["max_trainings_per_month"],
                "max_llm_calls_per_day": free_plan["max_llm_calls_per_day"],
                "max_sources_connected": free_plan["max_sources_connected"],
                "auto_sync_enabled": free_plan["auto_sync_enabled"],
                "features": free_plan["features"],
                "expires_at": None,
            }
        
        # Fallback
        return {
            "plan_name": "free",
            "max_trainings_per_month": 10,
            "max_llm_calls_per_day": 5,
            "max_sources_connected": 0,
            "auto_sync_enabled": False,
            "features": {},
        }
    
    @staticmethod
    def check_quota(user_id: int, action_type: str) -> Dict[str, Any]:
        """Проверяет, не превышена ли квота для действия.
        
        Returns:
            {
                "allowed": bool,
                "message": str (если запрещено),
                "used": int,
                "limit": int
            }
        """
        plan = SubscriptionManager.get_user_plan(user_id)
        
        if action_type == "llm_call":
            limit = plan.get("max_llm_calls_per_day", -1)
            if limit == -1:
                return {"allowed": True, "used": 0, "limit": -1}
            
            # Считаем за последние 24 часа
            since = datetime.now() - timedelta(days=1)
            used = get_usage_count(user_id, "llm_call", since)
            
            if used >= limit:
                return {
                    "allowed": False,
                    "message": f"Дневной лимит LLM-запросов исчерпан ({used}/{limit}). Обновите подписку.",
                    "used": used,
                    "limit": limit,
                }
            return {"allowed": True, "used": used, "limit": limit}
        
        elif action_type == "training_created":
            limit = plan.get("max_trainings_per_month", -1)
            if limit == -1:
                return {"allowed": True, "used": 0, "limit": -1}
            
            # Считаем за текущий месяц
            now = datetime.now()
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            used = get_usage_count(user_id, "training_created", since)
            
            if used >= limit:
                return {
                    "allowed": False,
                    "message": f"Месячный лимит тренировок исчерпан ({used}/{limit}). Обновите подписку.",
                    "used": used,
                    "limit": limit,
                }
            return {"allowed": True, "used": used, "limit": limit}
        
        elif action_type == "sync":
            if not plan.get("features", {}).get("sync", False):
                return {
                    "allowed": False,
                    "message": "Синхронизация доступна на платных тарифах.",
                    "used": 0,
                    "limit": 0,
                }
            return {"allowed": True, "used": 0, "limit": -1}
        
        # По умолчанию разрешаем
        return {"allowed": True, "used": 0, "limit": -1}
    
    @staticmethod
    def log_action(user_id: int, action_type: str, metadata: Optional[Dict] = None):
        """Логирует действие для отслеживания квот."""
        log_usage(user_id, action_type, metadata)
    
    @staticmethod
    def has_feature(user_id: int, feature_name: str) -> bool:
        """Проверяет, есть ли у пользователя определенная фича."""
        plan = SubscriptionManager.get_user_plan(user_id)
        features = plan.get("features", {})
        
        # Если features — строка (JSON), парсим
        if isinstance(features, str):
            import json
            try:
                features = json.loads(features)
            except:
                features = {}
        
        return features.get(feature_name, False)
    
    @staticmethod
    def subscribe_user(user_id: int, plan_name: str, duration_days: int = 30) -> Optional[int]:
        """Подписывает пользователя на план. Возвращает ID подписки."""
        plan = get_plan_by_name(plan_name)
        if not plan:
            logger.error(f"Plan {plan_name} not found")
            return None
        
        sub_id = create_subscription(user_id, plan["id"], duration_days)
        logger.info(f"User {user_id} subscribed to {plan_name} (sub_id={sub_id})")
        return sub_id


# Глобальный экземпляр для удобства
sub_manager = SubscriptionManager()