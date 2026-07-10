import { SymbolView } from 'expo-symbols';
import { Tabs } from 'expo-router';
import { Platform } from 'react-native';

import Colors from '@/constants/Colors';
import { useColorScheme } from '@/components/useColorScheme';
import { useClientOnlyValue } from '@/components/useClientOnlyValue';

export default function TabLayout() {
  const colorScheme = useColorScheme();

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: Colors[colorScheme].tint,
        headerShown: useClientOnlyValue(false, true),
      }}>
      
      {/* Hide the index redirect route */}
      <Tabs.Screen
        name="index"
        options={{
          href: null,
        }}
      />
      
      {/* Hide the default two route */}
      <Tabs.Screen
        name="two"
        options={{
          href: null,
        }}
      />

      <Tabs.Screen
        name="capture"
        options={{
          title: 'Scan Prescription',
          tabBarIcon: ({ color }) => (
            <SymbolView
              name={{ ios: 'camera', android: 'camera', web: 'camera' }}
              tintColor={color}
              size={28}
              fallback={Platform.OS === 'android' ? 'camera' : undefined}
            />
          ),
        }}
      />
      
      <Tabs.Screen
        name="review"
        options={{
          title: 'Review Data',
          tabBarIcon: ({ color }) => (
            <SymbolView
              name={{ ios: 'doc.text.magnifyingglass', android: 'text-snippet', web: 'text-snippet' }}
              tintColor={color}
              size={28}
            />
          ),
        }}
      />
    </Tabs>
  );
}
