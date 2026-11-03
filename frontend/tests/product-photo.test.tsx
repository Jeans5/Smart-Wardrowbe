import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BulkActionToolbar, type BulkSelection } from '@/components/bulk-action-toolbar'
import { isProductPhotoMode } from '@/lib/hooks/use-features'

const selection: BulkSelection = { mode: 'some', selectedIds: new Set(['a']), excludedIds: new Set() }

function renderToolbar(productPhoto?: boolean) {
  render(
    <BulkActionToolbar
      selection={selection}
      totalItems={5}
      pageItems={5}
      onSelectAll={vi.fn()}
      onSelectAllMatching={vi.fn()}
      onClear={vi.fn()}
      onDelete={vi.fn()}
      onRemoveBackground={vi.fn()}
      productPhoto={productPhoto}
    />,
  )
}

describe('isProductPhotoMode', () => {
  it('is on only for the product photo mode', () => {
    expect(isProductPhotoMode({ background_removal_mode: 'product_photo' })).toBe(true)
    expect(isProductPhotoMode({ background_removal_mode: 'cutout' })).toBe(false)
    expect(isProductPhotoMode({ background_removal_mode: null })).toBe(false)
    // A server from before the field existed.
    expect(isProductPhotoMode({})).toBe(false)
    expect(isProductPhotoMode(undefined)).toBe(false)
  })
})

describe('BulkActionToolbar background action', () => {
  it('offers a product photo in product photo mode', () => {
    renderToolbar(true)
    expect(screen.getByLabelText('bulkActions.makeProductPhoto')).toBeInTheDocument()
    expect(screen.queryByLabelText('bulkActions.removeBackground')).toBeNull()
  })

  it('keeps background removal otherwise', () => {
    renderToolbar()
    expect(screen.getByLabelText('bulkActions.removeBackground')).toBeInTheDocument()
    expect(screen.queryByLabelText('bulkActions.makeProductPhoto')).toBeNull()
  })
})
