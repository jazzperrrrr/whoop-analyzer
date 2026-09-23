import type { TextStyle } from 'react-native';

// Semantic roles keep a future dark palette independent of feature code.
export const colors = {
  background: '#F8F8F5', surface: '#FFFFFF', text: '#202925', muted: '#626C66',
  accent: '#37594A', accentSoft: '#E8EEE9', line: '#E3E6E0',
  lightStage: '#BCCDC2', deepStage: '#456453', remStage: '#839F8E',
};
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48, huge: 64 };
export const radius = { small: 12, medium: 20, large: 28 };
export const motion = { quick: 120, standard: 220, hero: { damping: 24, stiffness: 180, mass: 1 } };
export const typography = {
  hero: { fontSize: 56, lineHeight: 64, fontWeight: '500', letterSpacing: -2 },
  metric: { fontSize: 36, lineHeight: 44, fontWeight: '500', letterSpacing: -1 },
  title: { fontSize: 36, lineHeight: 44, fontWeight: '500', letterSpacing: -1 },
  section: { fontSize: 22, lineHeight: 30, fontWeight: '500', letterSpacing: -0.4 },
  body: { fontSize: 16, lineHeight: 24 },
  caption: { fontSize: 13, lineHeight: 20 },
} satisfies Record<string, TextStyle>;
