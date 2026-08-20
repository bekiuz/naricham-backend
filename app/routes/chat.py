from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.manus import (
    ManusApiError,
    ManusClient,
    ManusConfigurationError,
    ManusInvalidResponseError,
    ManusTimeoutError,
)
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


def get_manus_client(request: Request) -> ManusClient:
    return request.app.state.manus_client


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    manus: ManusClient = Depends(get_manus_client),
) -> ChatResponse:
    try:
        return await manus.send_chat(payload.message, task_id=payload.task_id)
    except ManusConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "manus_not_configured",
                "message": "Manus API is not configured on this server",
            },
        ) from exc
    except ManusTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "manus_timeout",
                "message": "Manus API request timed out",
            },
        ) from exc
    except ManusInvalidResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "invalid_manus_response",
                "message": str(exc),
            },
        ) from exc
    except ManusApiError as exc:
        detail = (
            exc.diagnostic_detail()
            if manus.diagnostics_enabled
            else exc.public_detail()
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from exc
