# PCI SERVER Setup Guide

<!-- ## Prerequisites

- Node.js (18+)
- Postgresql Database
- Prisma ORM
- Google cloud console account (for OAUTH)
- Nodemailer user and password

## Installation Steps

### 1. Initialize Project

```bash
npm install
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`
and fill in your values:

```bash
cp .env.example .env
```

### 3. Setup Prisma

```bash
# Generate Prisma Client
npx prisma generate

# Run migrations (create tables and relationships)
npx prisma migrate dev --name < name of migration >

# (Optional) Open Prisma Studio
npx prisma studio
```

**Important:**
After Pulling new changes that includes schema updates, always run:

```bash
npx prisma generate #
npx prisma migrate dev --name < name of migration > #
Apply database migrations
```

### 4. Setup Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Google+ API
4. Create OAuth 2.0 credentials
5. Add authorized redirect URIs: `http://localhost:8000/api/auth/google/callback`
6. Copy Client ID and Client Secret to `.env`

### 5. Start Development Server

```bash
npm run dev
```

## API Endpoints

<!--  -->

### POST `/api/auth/signup`

Register a new user.

**Request Body:**

```json
{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "firstName": "John",
  "lastName": "Doe"
}
```

**Response:**

```json
{
  "message": "User created successfully. Please check your email for verification code.",
  "userId": "uuid"
}
```

<!--  -->

<!--  -->

### POST `/api/auth/signin`

Authenticate user and get tokens.

**Request Body:**

```json
{
  "email": "user@example.com",
  "password": "SecurePass123!"
}
```

**Response:**

```json
{
  "message": "Sign in successful",
  "token": "jwt_token",
  "refreshToken": "refresh_token",
  "user": {
    "id": "cuid",
    "email": "user@example.com",
    "firstName": "John ",
    "lastName": "Doe",
    "emailVerified": true
  }
}
```

<!--  -->

<!--  -->

### POST `/api/auth/signout`

Sign out user (requires authentication).

<!-- **Headers:**
```
Authorization: Bearer <token>
``` -->

**Response:**

```json
{
  "message": "Sign out successful"
}
```

<!--  -->

<!--  -->

### POST `/api/auth/verify-email`

Verify email with OTP code.

**Request Body:**

```json
{
  "email": "user@example.com",
  "code": "123456"
}
```

**Response:**

```json
{
  "message": "Email verified successfully",
  "token": "jwt_token",
  "refreshToken": "refresh_token"
}
```

<!--  -->

<!--  -->

### POST `/api/auth/resend-otp`

Resend verification code.

**Request Body:**

```json
{
  "email": "user@example.com"
}
```

**Response:**

```json
{
  "message": "Verification code sent successfully"
}
```

<!--  -->

<!--  -->

### POST `/api/auth/forgot-password`

Request password reset link.

**Request Body:**

```json
{
  "email": "user@example.com"
}
```

**Response:**

```json
{
  "message": "If the email exists, a reset link has been sent"
}
```

<!--  -->

<!--  -->

### POST `/api/auth/reset-password`

Reset password with token.

**Request Body:**

```json
{
  "token": "reset_token",
  "password": "NewSecurePass123!"
}
```

**Response:**

```json
{
  "message": "Password reset successful"
}
```

<!--  -->

<!--  -->

### GET `/api/auth/google`

Get Google OAuth URL.

**Redirects to:**

```
   https://accounts.google.com/o/oauth2/v2/auth?...

```

<!--  -->

<!--  -->

### GET `/api/auth/google/callback`

Google OAuth callback (redirects to client with tokens).

**Redirects to:**

```
{CLIENT_URL}/auth/callback?token={jwt}&refreshToken={refresh}
```

<!--  -->


<!--  -->

### GET `/api/auth/facebook`

Get Facebook OAuth URL.

**Redirects to:**

```
   https://accounts.facebook.com/o/oauth2/v2/auth?...

```

<!--  -->

<!--  -->

### GET `/api/auth/facebook/callback`

Facebook OAuth callback (redirects to client with tokens).

**Redirects to:**

```
{CLIENT_URL}/auth/callback?token={jwt}&refreshToken={refresh}
``` -->

<!--  -->