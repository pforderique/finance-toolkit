"""KHeap: A min- or max-heap implementation limiting to k elements.

A min- or max-heap implementation using Python's heapq module.
This implementation allows for both min-heap and max-heap behavior.
It uses a wrapper class to reverse the order for max-heap.
"""

from typing import TypeVar, Generic, Self

import heapq


T = TypeVar('T', int, float)

class KHeap(Generic[T]):
    """
    A min- or max-heap tracking at most k elements.

    Elements must be number types (int, float). If the heap exceeds size k,
    the top() element is first removed to maintain the size, and the new value
    is added.

    Example usage:
    >>> min_heap = KHeap[int](3, is_min_heap=True)
    >>> min_heap.push(5).push(2).push(4)
    >>> min_heap.top()
    2
    >>> min_heap.push(3)  # This will first remove 2 to maintain size k=3
    >>> min_heap.top()
    3
    >>> min_heap.push(1)  # This will first remove 3 to maintain size k=3
    >>> min_heap.top()
    1
    """

    def __init__(self, k: int, is_min_heap: bool = True):
        self._min = is_min_heap
        self._heap: list[T] = []
        self._k = k

    def push(self, value: T) -> Self:
        """Push a value onto the heap, maintaining size k.
        
        If the heap exceeds size k, the top element is removed before adding the
        new value.
        """
        # First remove the top element if the heap is full
        if len(self._heap) == self._k:
            heapq.heappop(self._heap)

        if self._min:
            heapq.heappush(self._heap, value)
        else:
            heapq.heappush(self._heap, -value)  # type: ignore

        return self

    def pop(self) -> T:
        """Pop the top element from the heap.
        
        Raises IndexError if the heap is empty.
        """
        if not self._heap:
            raise IndexError("Heap is empty")

        if self._min:
            return heapq.heappop(self._heap)
        else:
            return -heapq.heappop(self._heap)

    def top(self) -> T:
        """Return the top element of the heap without removing it."""
        if not self._heap:
            raise IndexError("Heap is empty")

        if self._min:
            return self._heap[0]
        else:
            return -self._heap[0]

    def clear(self) -> None:
        """Clear the heap."""
        self._heap.clear()

    def size(self) -> int:
        """Return the current size of the heap."""
        return len(self)

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)

    def __repr__(self) -> str:
        if self._min:
            return f"KHeap(min, k={self._k}, size={len(self._heap)}, elements={self._heap})"
        else:
            return f"KHeap(max, k={self._k}, size={len(self._heap)}, elements={[-x for x in self._heap]})"