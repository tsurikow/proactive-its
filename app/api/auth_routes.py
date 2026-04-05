from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_auth_service,
    get_current_authenticated_learner,
)
from app.api.schemas import (
    AuthErrorResponse,
    AuthLearner,
    AuthResponse,
    BasicSuccessResponse,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SignupRequest,
)
from app.auth.service import AuthService, AuthenticatedLearner, AuthServiceError
from app.platform.config import get_settings

router = APIRouter(prefix="/auth")


def _set_auth_cookie(response: Response, session_token: str) -> None:
    settings = get_settings()
    max_age = settings.auth_session_ttl_hours * 60 * 60
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _to_auth_response(learner: AuthenticatedLearner) -> AuthResponse:
    return AuthResponse(
        learner=AuthLearner(
            id=learner.id,
            first_name=learner.first_name,
            last_name=learner.last_name,
            display_name=f"{learner.first_name} {learner.last_name}".strip(),
            email=learner.email,
            is_active=learner.is_active,
        )
    )


def _auth_error_response(error: AuthServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=AuthErrorResponse(
            code=error.code,
            message=error.message,
            field_errors=error.field_errors,
        ).model_dump(exclude_none=True),
    )


@router.post("/signup", response_model=AuthResponse)
async def signup(
    request: SignupRequest,
    response: Response,
    raw_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    try:
        await auth_service.require_same_origin(raw_request)
        result = await auth_service.signup(
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            password=request.password,
            user_agent=raw_request.headers.get("user-agent"),
            ip_address=raw_request.client.host if raw_request.client else None,
        )
    except AuthServiceError as exc:
        return _auth_error_response(exc)
    _set_auth_cookie(response, result.session_token)
    return _to_auth_response(result.learner)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    response: Response,
    raw_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    try:
        await auth_service.require_same_origin(raw_request)
        result = await auth_service.login(
            email=request.email,
            password=request.password,
            user_agent=raw_request.headers.get("user-agent"),
            ip_address=raw_request.client.host if raw_request.client else None,
        )
    except AuthServiceError as exc:
        return _auth_error_response(exc)
    _set_auth_cookie(response, result.session_token)
    return _to_auth_response(result.learner)


@router.post("/logout", response_model=BasicSuccessResponse)
async def logout(
    response: Response,
    raw_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> BasicSuccessResponse:
    try:
        await auth_service.require_same_origin(raw_request)
    except AuthServiceError as exc:
        return _auth_error_response(exc)
    await auth_service.logout(raw_request.cookies.get(get_settings().auth_cookie_name))
    _clear_auth_cookie(response)
    return BasicSuccessResponse()


@router.post("/password-reset/request", response_model=BasicSuccessResponse)
async def password_reset_request(
    request: PasswordResetRequest,
    raw_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> BasicSuccessResponse:
    try:
        await auth_service.require_same_origin(raw_request)
        await auth_service.issue_password_reset(email=request.email)
    except AuthServiceError as exc:
        return _auth_error_response(exc)
    return BasicSuccessResponse()


@router.post("/password-reset/confirm", response_model=BasicSuccessResponse)
async def password_reset_confirm(
    request: PasswordResetConfirmRequest,
    raw_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> BasicSuccessResponse:
    try:
        await auth_service.require_same_origin(raw_request)
        await auth_service.confirm_password_reset(token=request.token, new_password=request.new_password)
    except AuthServiceError as exc:
        return _auth_error_response(exc)
    return BasicSuccessResponse()


@router.get("/me", response_model=AuthResponse)
async def me(
    learner: AuthenticatedLearner | None = Depends(get_current_authenticated_learner),
) -> AuthResponse:
    if learner is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return _to_auth_response(learner)
