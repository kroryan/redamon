/**
 * Invariants for the AI Attack Surface shared vocabulary.
 * @vitest-environment node
 */
import { describe, test, expect } from 'vitest'
import {
  ALL_CARDS, ATTACK_CHIPS, FUTURE_CARDS, GARAK_CARD,
  type ChipKey,
} from './aiAttackSurface'

const CHIP_KEYS = Object.keys(ATTACK_CHIPS) as ChipKey[]

describe('ATTACK_CHIPS', () => {
  test('has 8 chips, each fully specified', () => {
    expect(CHIP_KEYS).toHaveLength(8)
    for (const k of CHIP_KEYS) {
      const c = ATTACK_CHIPS[k]
      expect(c.label).toBeTruthy()
      expect(c.color).toMatch(/^#[0-9a-f]{6}$/i)
      expect(c.owasp).toBeTruthy()
      expect(c.definition).toBeTruthy()
    }
  })
})

describe('cards', () => {
  test('every card chip is a known chip key', () => {
    for (const card of ALL_CARDS) {
      for (const chip of card.chips) {
        expect(CHIP_KEYS).toContain(chip)
      }
    }
  })

  test('garak probes map to known chip keys', () => {
    for (const p of GARAK_CARD.probes) {
      expect(CHIP_KEYS).toContain(p.chip)
      expect(p.id).toBeTruthy()
    }
  })

  test('garak is the only available card; the rest are greyed (future)', () => {
    expect(GARAK_CARD.available).toBe(true)
    expect(FUTURE_CARDS.every((c) => !c.available)).toBe(true)
    expect(ALL_CARDS).toHaveLength(4)
    expect(ALL_CARDS[0]).toBe(GARAK_CARD)
  })

  test('garak probe families match the documented MVP set', () => {
    const ids = GARAK_CARD.probes.map((p) => p.id).sort()
    expect(ids).toEqual(['dan', 'encoding', 'leakreplay', 'promptinject'])
  })

  test('card ids are unique', () => {
    const ids = ALL_CARDS.map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
