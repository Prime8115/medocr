/**
 * Runtime configuration. The backend URL is environment-driven — set
 * EXPO_PUBLIC_API_URL in `.env` or the build environment. Never hardcode a LAN IP.
 */
export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8080';

export const LOW_CONFIDENCE_THRESHOLD = 0.6;

/** Max image dimension (px) after client-side compression before upload. */
export const MAX_IMAGE_DIMENSION = 2000;
export const IMAGE_COMPRESSION = 0.7;
