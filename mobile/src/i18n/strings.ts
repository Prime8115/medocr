/** Minimal i18n. English default; Hindi-ready. Set locale in Settings. */
export type Locale = 'en' | 'hi';

const en = {
  appName: 'MediScan',
  // Auth
  login: 'Log in',
  logout: 'Log out',
  email: 'Email',
  password: 'Password',
  loginError: 'Incorrect email or password',
  // Capture
  scan: 'Scan',
  gallery: 'Gallery',
  pdf: 'PDF',
  retake: 'Retake',
  process: 'Process',
  prescription: 'Prescription',
  invoice: 'Invoice',
  autoDetect: 'Auto-detect',
  cameraPermission: 'We need camera access to scan documents',
  grantPermission: 'Grant permission',
  framingHint: 'Fit the document inside the frame',
  // Review
  review: 'Review',
  patient: 'Patient',
  prescriber: 'Prescriber',
  medications: 'Medications',
  supplier: 'Supplier',
  invoiceDetails: 'Invoice',
  lineItems: 'Line items',
  save: 'Save',
  approve: 'Approve',
  approveAndPush: 'Approve & Send',
  push: 'Send to software',
  lowConfidence: 'Please check — low confidence',
  processing: 'Processing…',
  // History
  history: 'History',
  search: 'Search',
  all: 'All',
  empty: 'Nothing here yet',
  queued: 'Queued',
  // Status
  offline: 'Offline — saved to queue',
  syncing: 'Syncing…',
  failed: 'Failed',
  needsReview: 'Needs review',
  approved: 'Approved',
  pushed: 'Sent',
  retry: 'Retry',
  // Generic
  cancel: 'Cancel',
  ok: 'OK',
  errorGeneric: 'Something went wrong',
};

const hi: typeof en = {
  ...en,
  // Hindi overrides (partial; fall back to English where absent).
  login: 'लॉग इन करें',
  logout: 'लॉग आउट',
  email: 'ईमेल',
  password: 'पासवर्ड',
  scan: 'स्कैन',
  gallery: 'गैलरी',
  process: 'प्रोसेस',
  prescription: 'पर्ची',
  invoice: 'बिल',
  review: 'समीक्षा',
  patient: 'मरीज़',
  medications: 'दवाइयाँ',
  save: 'सेव',
  approve: 'स्वीकृत',
  history: 'इतिहास',
  search: 'खोजें',
  queued: 'कतार में',
  retry: 'पुनः प्रयास',
};

const catalogs: Record<Locale, typeof en> = { en, hi };

let current: Locale = 'en';
export function setLocale(l: Locale) {
  current = l;
}
export function getLocale(): Locale {
  return current;
}
export function t(key: keyof typeof en): string {
  return catalogs[current][key] ?? en[key];
}
export type StringKey = keyof typeof en;
