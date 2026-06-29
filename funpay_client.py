"""
funpay_client.py — Асинхронный клиент для работы с FunPay через httpx + BeautifulSoup4.
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://funpay.com"

HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Ch-Ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}

HEADERS_XHR = {
    **HEADERS_BASE,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
}


@dataclass
class ParsedOrder:
    order_id: str
    product_funpay_id: str
    buyer_username: str
    buyer_id: str
    chat_id: str


@dataclass
class ParsedChat:
    chat_id: str
    username: str
    last_message: str
    unread: bool


class FunPayClient:
    """
    Асинхронный клиент для взаимодействия с FunPay.
    Использует golden_key cookie для аутентификации.
    """

    def __init__(self, golden_key: str, proxy: Optional[str] = None):
        self.golden_key = golden_key
        self.proxy = proxy
        self._client: Optional[httpx.AsyncClient] = None

    # ─── Жизненный цикл клиента ───────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Возвращает существующий httpx-клиент или создаёт новый."""
        if self._client is None or self._client.is_closed:
            cookies = httpx.Cookies()
            cookies.set("golden_key", self.golden_key, domain="funpay.com")
            self._client = httpx.AsyncClient(
                headers=HEADERS_BASE,
                cookies=cookies,
                proxy=self.proxy,
                follow_redirects=True,
                timeout=httpx.Timeout(20.0),
            )
        return self._client

    async def close(self) -> None:
        """Закрывает HTTP-сессию."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ─── Утилиты ──────────────────────────────────────────────────────────────

    def _parse_html(self, content: str) -> BeautifulSoup:
        return BeautifulSoup(content, "html.parser")

    async def _get_page(self, url: str, params: dict = None) -> Optional[BeautifulSoup]:
        """GET-запрос, возвращает распарсенный HTML или None при ошибке."""
        try:
            client = await self._get_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return self._parse_html(resp.text)
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP ошибка при GET {url}: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Сетевая ошибка при GET {url}: {e}")
        return None

    async def _extract_csrf_token(self, soup: BeautifulSoup) -> Optional[str]:
        """Извлекает CSRF-токен из мета-тега или скрипта страницы."""
        # Вариант 1: <meta name="csrf-token" content="...">
        meta = soup.find("meta", {"name": "csrf-token"})
        if meta and meta.get("content"):
            return meta["content"]

        # Вариант 2: app.init({ "csrf": "..." }) в inline-скрипте
        for script in soup.find_all("script"):
            text = script.string or ""
            match = re.search(r'"csrf"\s*:\s*"([a-f0-9]+)"', text)
            if match:
                return match.group(1)

        # Вариант 3: скрытый input
        inp = soup.find("input", {"name": "_token"})
        if inp and inp.get("value"):
            return inp["value"]

        return None

    # ─── Аутентификация ───────────────────────────────────────────────────────

    async def validate_golden_key(self) -> Optional[str]:
        """
        Проверяет валидность golden_key.
        Возвращает имя пользователя если ключ валиден, иначе None.
        """
        soup = await self._get_page(f"{BASE_URL}/")
        if soup is None:
            return None

        # Имя пользователя в шапке сайта
        username_tag = soup.select_one("div.user-link-name") or soup.select_one("a.username")
        if username_tag:
            username = username_tag.get_text(strip=True)
            if username:
                logger.info(f"golden_key валиден, пользователь: {username}")
                return username

        # Альтернативный селектор
        user_block = soup.select_one("div.top-user-info span.username")
        if user_block:
            return user_block.get_text(strip=True)

        logger.warning("Не удалось определить пользователя — возможно, ключ недействителен.")
        return None

    # ─── Чаты ─────────────────────────────────────────────────────────────────

    async def get_chats(self) -> list[ParsedChat]:
        """
        Парсит список чатов с https://funpay.com/chat/.
        Возвращает список ParsedChat.
        """
        soup = await self._get_page(f"{BASE_URL}/chat/")
        if soup is None:
            return []

        chats: list[ParsedChat] = []

        # Каждый чат — элемент <a class="contact-item ...">
        for item in soup.select("a.contact-item"):
            try:
                chat_id = item.get("data-id", "").strip()
                username_tag = item.select_one("div.media-user-name") or item.select_one(".contact-name")
                last_msg_tag = item.select_one("div.contact-item-message") or item.select_one(".last-message")
                unread = "unread" in (item.get("class") or [])

                username = username_tag.get_text(strip=True) if username_tag else "unknown"
                last_message = last_msg_tag.get_text(strip=True) if last_msg_tag else ""

                if chat_id:
                    chats.append(ParsedChat(
                        chat_id=chat_id,
                        username=username,
                        last_message=last_message,
                        unread=unread,
                    ))
            except Exception as e:
                logger.warning(f"Ошибка парсинга чата: {e}")

        return chats

    async def send_message(self, chat_id: str, text: str) -> bool:
        """
        Отправляет сообщение в указанный чат FunPay.
        Возвращает True при успехе.
        """
        # Сначала получаем страницу чата для CSRF-токена
        soup = await self._get_page(f"{BASE_URL}/chat/", params={"node": chat_id})
        if soup is None:
            return False

        csrf = await self._extract_csrf_token(soup)
        if not csrf:
            logger.error(f"Не удалось получить CSRF-токен для чата {chat_id}")
            return False

        payload = {
            "action": "chat_message",
            "data": {
                "node": chat_id,
                "message": text,
                "csrf": csrf,
            },
        }

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{BASE_URL}/chat/post/",
                json=payload,
                headers={**HEADERS_XHR, "Referer": f"{BASE_URL}/chat/?node={chat_id}"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("type") == "ok":
                logger.info(f"Сообщение отправлено в чат {chat_id}")
                return True
            else:
                logger.warning(f"FunPay вернул ошибку при отправке: {data}")
                return False
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке сообщения: {e}")
            return False

    # ─── Заказы ───────────────────────────────────────────────────────────────

    async def get_paid_orders(self) -> list[ParsedOrder]:
        """
        Парсит страницу заказов https://funpay.com/orders/trade
        и возвращает список оплаченных заказов.
        """
        soup = await self._get_page(f"{BASE_URL}/orders/trade")
        if soup is None:
            return []

        orders: list[ParsedOrder] = []

        for row in soup.select("a.tc-item"):
            try:
                # Статус заказа
                status_tag = row.select_one("div.tc-status")
                status_text = status_tag.get_text(strip=True).lower() if status_tag else ""

                # Считаем оплаченными статусы: "оплачен", "paid", "processing"
                is_paid = any(s in status_text for s in ("оплачен", "paid", "ожидает", "processing"))
                if not is_paid:
                    continue

                # ID заказа из href: /orders/trade/ORDER_ID/
                href = row.get("href", "")
                order_match = re.search(r"/orders/trade/([^/]+)/", href)
                if not order_match:
                    continue
                order_id = order_match.group(1)

                # Покупатель
                buyer_tag = row.select_one("div.media-user-name") or row.select_one(".tc-user span")
                buyer_username = buyer_tag.get_text(strip=True) if buyer_tag else "unknown"

                # ID покупателя из ссылки на профиль
                buyer_link = row.select_one("a[href*='/users/']")
                buyer_id = ""
                if buyer_link:
                    bid_match = re.search(r"/users/(\d+)-", buyer_link.get("href", ""))
                    if bid_match:
                        buyer_id = bid_match.group(1)

                # ID товара/оффера из описания заказа
                offer_tag = row.select_one("div.tc-desc-text") or row.select_one(".offer-list-title")
                product_funpay_id = ""
                if offer_tag:
                    offer_link = offer_tag.find_parent("a") or row.find("a", href=re.compile(r"/lots/offer\?id="))
                    if offer_link:
                        oid_match = re.search(r"id=(\d+)", offer_link.get("href", ""))
                        if oid_match:
                            product_funpay_id = oid_match.group(1)

                # chat_id совпадает с buyer_id на FunPay
                chat_id = buyer_id or order_id

                orders.append(ParsedOrder(
                    order_id=order_id,
                    product_funpay_id=product_funpay_id,
                    buyer_username=buyer_username,
                    buyer_id=buyer_id,
                    chat_id=chat_id,
                ))

            except Exception as e:
                logger.warning(f"Ошибка парсинга строки заказа: {e}")

        return orders

    # ─── Поднятие лотов ───────────────────────────────────────────────────────

    async def get_my_offers(self) -> list[dict]:
        """
        Парсит страницу моих лотов https://funpay.com/users/USER_ID/lots/
        Возвращает список словарей с id и node лотов.
        """
        # Сначала получаем ID пользователя из главной страницы
        soup_main = await self._get_page(f"{BASE_URL}/")
        if soup_main is None:
            return []

        user_id = None
        profile_link = soup_main.select_one("a[href*='/users/']")
        if profile_link:
            uid_match = re.search(r"/users/(\d+)-", profile_link.get("href", ""))
            if uid_match:
                user_id = uid_match.group(1)

        if not user_id:
            logger.error("Не удалось определить ID пользователя для получения лотов.")
            return []

        soup = await self._get_page(f"{BASE_URL}/users/{user_id}/lots/")
        if soup is None:
            return []

        offers = []
        for item in soup.select("a.tc-item"):
            href = item.get("href", "")
            nid_match = re.search(r"/lots/(\d+)/", href)
            oid_match = re.search(r"offer\?id=(\d+)", href)
            if nid_match or oid_match:
                offers.append({
                    "node_id": nid_match.group(1) if nid_match else "",
                    "offer_id": oid_match.group(1) if oid_match else "",
                    "href": href,
                })

        return offers

    async def raise_lots(self) -> bool:
        """
        Поднимает все активные лоты пользователя.
        Возвращает True если хотя бы один лот успешно поднят.
        """
        # Получаем главную страницу для CSRF
        soup = await self._get_page(f"{BASE_URL}/")
        if soup is None:
            return False

        csrf = await self._extract_csrf_token(soup)
        if not csrf:
            logger.error("Не удалось получить CSRF-токен для поднятия лотов.")
            return False

        offers = await self.get_my_offers()
        if not offers:
            logger.info("Нет лотов для поднятия.")
            return False

        # Собираем node_id уникальные значения для поднятия
        node_ids = list({o["node_id"] for o in offers if o["node_id"]})
        success = False

        for node_id in node_ids:
            payload = {
                "action": "offers",
                "offers": [],
                "node_id": node_id,
                "csrf": csrf,
            }
            try:
                client = await self._get_client()
                resp = await client.post(
                    f"{BASE_URL}/lots/raise",
                    json=payload,
                    headers={**HEADERS_XHR, "Referer": f"{BASE_URL}/"},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("type") == "ok":
                    logger.info(f"Лоты в категории {node_id} успешно подняты.")
                    success = True
                else:
                    logger.warning(f"FunPay вернул ответ для node {node_id}: {data}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.error(f"Ошибка при поднятии лотов node={node_id}: {e}")

        return success
