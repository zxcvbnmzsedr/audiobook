import { create } from 'zustand'
import type { Book } from './types'

type WorkspaceState = {
  currentBookId: string | null
  currentBook: Book | null
  setCurrentBook: (book: Book | null) => void
  setCurrentBookId: (bookId: string | null) => void
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  currentBookId: null,
  currentBook: null,
  setCurrentBook: (book) => set({ currentBook: book, currentBookId: book?.id ?? null }),
  setCurrentBookId: (bookId) => set({ currentBookId: bookId }),
}))
