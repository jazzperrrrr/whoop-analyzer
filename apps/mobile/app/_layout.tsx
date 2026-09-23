import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { Platform, View } from 'react-native';
import { ApiProvider } from '../src/api/provider';
import { colors } from '../src/design/tokens';
import { useReducedMotion } from '../src/design/useReducedMotion';

export default function RootLayout() {
  const reduced = useReducedMotion();
  return <ApiProvider>
    <StatusBar style="dark"/>
    <View style={{ flex: 1, width: '100%', alignSelf: 'center', ...(Platform.OS === 'web' ? { maxWidth: 440 } : {}) }}>
    <Stack screenOptions={{ headerShadowVisible: false, headerStyle: { backgroundColor: colors.background }, headerTintColor: colors.text, contentStyle: { backgroundColor: colors.background }, animation: reduced ? 'none' : 'slide_from_right' }}>
      <Stack.Screen name="index" options={{ title: 'Today' }}/>
      <Stack.Screen name="sleep" options={{ title: 'Sleep', headerBackTitle: 'Today' }}/>
    </Stack>
    </View>
  </ApiProvider>;
}
