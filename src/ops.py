from __future__ import annotations

# Persisted operations (APQ) из логов клиента Twitch.
# Если значения sha256 поменяются — обнови их здесь.

OP = {
    # Инвентарь / прогресс
    "Inventory": {
        "op": "Inventory",
        "sha256": "d86775d0ef16a63a33ad52e80eaff963b2d5b72fada7c991504a57496e1d8e4b",
        "version": 1,
    },
    # Дэшборд Drops (список кампаний/прогресса)
    "ViewerDropsDashboard": {
        "op": "ViewerDropsDashboard",
        "sha256": "5a4da2ab3d5b47c9f9ce864e727b2cb346af1e3ea8b897fe8f704a97ff017619",
        "version": 1,
    },
    # Контекст текущей сессии (по каналу)
    "DropCurrentSessionContext": {
        "op": "DropCurrentSessionContext",
        "sha256": "4d06b702d25d652afb9ef835d2a550031f1cf762b193523a92166f40ea3d142b",
        "version": 1,
    },
    # Фиксируем «смотрю»
    "ChannelPage_SetSessionStatus": {
        "op": "ChannelPage_SetSessionStatus",
        "sha256": "8521e08af74c8cb5128e4bb99fa53b591391cb19492e65fb0489aeee2f96947f",
        "version": 1,
    },
    # Текущий пользователь
    "CoreAuthCurrentUser": {
        "op": "CoreAuthCurrentUser",
        "sha256": "bc444c5b28754cb660ed183236bb5fe083f2549d1804a304842dad846d51f3ee",
        "version": 1,
    },
    # Детали кампании (по dropID) — вытаскиваем game и dropInstanceID
    "DropCampaignDetails": {
        "op": "DropCampaignDetails",
        "sha256": "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
        "version": 1,
    },
    # Глобальный сервис подсветки дропов — как источник кампаний/каналов
    "DropsHighlightService_AvailableDrops": {
        "op": "DropsHighlightService_AvailableDrops",
        "sha256": "782dad0f032942260171d2d80a654f88bdd0c5a9dddc392e9bc92218a0f42d20",
        "version": 1,
    },
    # Получить userID по логину (для проверок)
    "GetUserIDFromLogin": {
        "op": "GetUserIDFromLogin",
        "sha256": "c8502d09d4f290bb5155e6953a2c3119d4296d7ce647a2e21d1cf4c805583e43",
        "version": 1,
    },
    # Проверка лайва канала
    "WithIsStreamLiveQuery": {
        "op": "WithIsStreamLiveQuery",
        "sha256": "04e46329a6786ff3a81c01c50bfa5d725902507a0deb83b0edbf7abe7a3716ea",
        "version": 1,
    },
    # Клейм награды — если sha256 неизвестен, используем текст запроса
    "ClaimDropRewards": {
        "op": "ClaimDropRewards",
        "sha256": None,
        "version": 1,
        "query": "mutation ClaimDropRewards($input: ClaimDropRewardsInput!) { claimDropRewards(input: $input) { status } }",
    },
}

def get_op(name: str) -> dict:
    item = OP.get(name)
    if not item:
        raise KeyError(f"Unknown op: {name}")
    return item

