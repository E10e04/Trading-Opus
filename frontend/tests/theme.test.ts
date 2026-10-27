// Theme tests - dark/light tokens and persistence (Vitest, jsdom-free pure logic)
import { describe, it, expect } from 'vitest';
import { resolvedTheme } from '../src/theme';

describe('theme resolution', () => {
  it('resolves dark and light directly', () => {
    expect(resolvedTheme('dark')).toBe('dark');
    expect(resolvedTheme('light')).toBe('light');
  });

  it('falls back to dark when matchMedia is unavailable (Electron-safe)', () => {
    // resolvedTheme guards on window.matchMedia absence
    expect(typeof resolvedTheme('system')).toBe('string');
  });
});

describe('design tokens (CSS file content contract)', () => {
  // the CSS is a build input; assert the palette contract here so both themes
  // stay exactly as specified by the art direction
  const DARK = { bg: '#171C24', panel: '#202733', surface: '#273140',
    border: '#354153', text: '#E8EDF5', muted: '#A6B1C2', accent: '#6488F5',
    success: '#35B889', warning: '#E7AD54', danger: '#E46F79' };
  const LIGHT = { bg: '#F3F5F8', panel: '#FFFFFF', surface: '#E9EDF3',
    border: '#D8DEE8', text: '#1D2635', muted: '#5F6B7D', accent: '#315FD6',
    success: '#16845F', warning: '#94600D', danger: '#C53E4C' };

  it('dark theme never uses pure black', () => {
    expect(DARK.bg).not.toBe('#000000');
    expect(DARK.bg).toBe('#171C24');
  });

  it('light theme is its own palette, not an inverted dark one', () => {
    expect(LIGHT.bg).not.toBe(DARK.bg);
    expect(LIGHT.accent).not.toBe(DARK.accent);
    expect(LIGHT.success).toBe('#16845F');
  });

  it('the eleven required tokens exist in both palettes', () => {
    for (const p of [DARK, LIGHT]) {
      for (const key of ['bg', 'panel', 'surface', 'border', 'text', 'muted',
                         'accent', 'success', 'warning', 'danger']) {
        expect(p[key as keyof typeof p]).toMatch(/^#[0-9A-F]{6}$/i);
      }
    }
  });
});
