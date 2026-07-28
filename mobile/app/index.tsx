import { Redirect } from 'expo-router';

// The root layout's auth guard handles redirects; default to the capture tab.
export default function Index() {
  return <Redirect href="/(tabs)/capture" />;
}
