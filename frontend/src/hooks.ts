import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { useWorkspaceStore } from './store'

export function useBooks() {
  const setCurrentBook = useWorkspaceStore((state) => state.setCurrentBook)
  return useQuery({
    queryKey: ['books'],
    queryFn: api.books,
    select: (data) => {
      const current = data.books.find((book) => book.id === data.current_book_id) ?? null
      setCurrentBook(current)
      return data
    },
  })
}
