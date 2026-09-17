import { useState, useEffect } from 'react'
import Papa from 'papaparse'

interface CSVResult<T> {
  data: T[]
  loading: boolean
  error: string | null
}

export function useCSV<T>(url: string): CSVResult<T> {
  const [data, setData] = useState<T[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    Papa.parse<T>(url, {
      download: true,
      header: true,
      skipEmptyLines: true,
      complete: (results) => {
        setData(results.data)
        setLoading(false)
      },
      error: (err) => {
        setError(err.message)
        setLoading(false)
      },
    })
  }, [url])

  return { data, loading, error }
}
