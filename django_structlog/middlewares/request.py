import asyncio
import uuid
from asgiref.sync import sync_to_async
import structlog
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.decorators import sync_and_async_middleware
from .. import signals
from typing import Callable
logger = structlog.getLogger(__name__)


def get_request_header(request, header_key, meta_key):
    if hasattr(request, "headers"):
        return request.headers.get(header_key)

    return request.META.get(meta_key)


class RequestMiddlewareBase:
    """``RequestMiddlewareBase`` adds request metadata to ``structlog``'s logger context automatically.

    >>> MIDDLEWARE = [
    ...     # ...
    ...     'django_structlog.middlewares.RequestMiddlewareBase',
    ... ]

    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._raised_exception = False


    @staticmethod
    def format_request(request):
        return "%s %s" % (request.method, request.get_full_path())

    def process_exception(self, request, exception):
        if isinstance(exception, (Http404, PermissionDenied)):
            # We don't log an exception here, and we don't set that we handled
            # an error as we want the standard `request_finished` log message
            # to be emitted.
            return

        self._raised_exception = True

        self.bind_user_id(request)
        signals.bind_extra_request_failed_metadata.send(
            sender=self.__class__,
            request=request,
            logger=logger,
            exception=exception,
        )
        logger.exception(
            "request_failed",
            code=500,
            request=self.format_request(request),
        )

    @staticmethod
    def bind_user_id(request):
        if hasattr(request, "user") and request.user is not None:
            user_id = None
            if hasattr(request.user, "pk"):
                user_id = request.user.pk
                if isinstance(user_id, uuid.UUID):
                    user_id = str(user_id)
            structlog.contextvars.bind_contextvars(user_id=user_id)

class AsyncRequestMiddleware(RequestMiddlewareBase):
    async def __call__(self, request):
        from ipware import get_client_ip

        print("AAAA running async")
        print("sta1")
        request_id = get_request_header(
            request, "x-request-id", "HTTP_X_REQUEST_ID"
        ) or str(uuid.uuid4())

        print("sta2")
        correlation_id = await sync_to_async(get_request_header(
            request, "x-correlation-id", "HTTP_X_CORRELATION_ID"
        ))()

        print("sta3")
        await sync_to_async(structlog.contextvars.bind_contextvars(request_id=request_id))()
        print("sta4")
        await sync_to_async(self.bind_user_id(request))()
        if correlation_id:
            print("sta5")
            await sync_to_async(structlog.contextvars.bind_contextvars(correlation_id=correlation_id))()

        print("sta6")
        ip, _ = await sync_to_async(get_client_ip(request))()
        print("sta7")
        await sync_to_async(structlog.contextvars.bind_contextvars(ip=ip))()
        print("sta8")
        await sync_to_async(signals.bind_extra_request_metadata.send(
            sender=self.__class__, request=request, logger=logger
        ))()

        print("sta9")
        await sync_to_async(logger.info(
            "request_started",
            request=self.format_request(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
        ))()
        self._raised_exception = False
        print("sta10")
        response = await sync_to_async(self.get_response(request))()
        if not self._raised_exception:
            print("sta11")
            await sync_to_async(self.bind_user_id(request))()
            print("sta12")
            await sync_to_async(signals.bind_extra_request_finished_metadata.send(
                sender=self.__class__,
                request=request,
                logger=logger,
                response=response,
            ))()
            print("sta13")
            await sync_to_async(logger.info(
                "request_finished",
                code=response.status_code,
                request=self.format_request(request),
            ))()

        print("sta14")
        await sync_to_async(structlog.contextvars.clear_contextvars())()
        return response

class SyncRequestMiddleware(RequestMiddlewareBase):
    def __call__(self, request):
        from ipware import get_client_ip

        print("AAAA running sync")
        request_id = get_request_header(
            request, "x-request-id", "HTTP_X_REQUEST_ID"
        ) or str(uuid.uuid4())

        correlation_id = get_request_header(
            request, "x-correlation-id", "HTTP_X_CORRELATION_ID"
        )

        structlog.contextvars.bind_contextvars(request_id=request_id)
        self.bind_user_id(request)
        if correlation_id:
            structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        ip, _ = get_client_ip(request)
        structlog.contextvars.bind_contextvars(ip=ip)
        signals.bind_extra_request_metadata.send(
            sender=self.__class__, request=request, logger=logger
        )

        logger.info(
            "request_started",
            request=self.format_request(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
        )
        self._raised_exception = False
        response = self.get_response(request)
        if not self._raised_exception:
            self.bind_user_id(request)
            signals.bind_extra_request_finished_metadata.send(
                sender=self.__class__,
                request=request,
                logger=logger,
                response=response,
            )
            logger.info(
                "request_finished",
                code=response.status_code,
                request=self.format_request(request),
            )

        structlog.contextvars.clear_contextvars()
        return response

@sync_and_async_middleware
def request_middleware(get_response: Callable, **kwargs) -> Callable:
    if asyncio.iscoroutinefunction(get_response):
        return AsyncRequestMiddleware(get_response)
    else:
        return SyncRequestMiddleware(get_response)