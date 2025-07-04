import pytest

from screener.core import heap


KHeap = heap.KHeap


class TestKHeap:
    def setup_method(self):
        self.min_heap = KHeap[int](k=3, is_min_heap=True)
        self.max_heap = KHeap[int](k=3, is_min_heap=False)

    def test_min_heap_order(self):
        self.min_heap.push(5).push(2).push(4)
        assert self.min_heap.top() == 2

    def test_min_heap_push_overflow(self):
        self.min_heap.push(5).push(2).push(4)
        self.min_heap.push(3)
        assert self.min_heap.top() == 3

    def test_min_heap_push_overflow_with_smaller(self):
        self.min_heap.push(5).push(2).push(4)
        self.min_heap.push(1)
        assert self.min_heap.top() == 1

    def test_max_heap_order(self):
        self.max_heap.push(5).push(2).push(4)
        assert self.max_heap.top() == 5

    def test_max_heap_push_overflow(self):
        self.max_heap.push(5).push(2).push(4)
        self.max_heap.push(3)
        assert self.max_heap.top() == 4

    def test_max_heap_push_overflow_with_larger(self):
        self.max_heap.push(5).push(2).push(4)
        self.max_heap.push(6)
        assert self.max_heap.top() == 6

    def test_min_heap_pop(self):
        self.min_heap.push(5).push(2).push(4)
        assert self.min_heap.pop() == 2
        assert self.min_heap.top() == 4

    def test_max_heap_pop(self):
        self.max_heap.push(5).push(2).push(4)
        assert self.max_heap.pop() == 5
        assert self.max_heap.top() == 4

    def test_heap_empty_pop(self):
        with pytest.raises(IndexError):
            self.min_heap.pop()

    def test_heap_empty_top(self):
        with pytest.raises(IndexError):
            self.min_heap.top()

    def test_heap_size(self):
        self.min_heap.push(5).push(2)
        assert self.min_heap.size() == 2

    def test_heap_size_invariant(self):
        self.min_heap.push(5).push(2).push(4)
        assert self.min_heap.size() == 3
        self.min_heap.push(1)
        assert self.min_heap.size() == 3

    def test_heap_clear(self):
        self.min_heap.push(5).push(2).push(4)
        self.min_heap.clear()
        assert self.min_heap.size() == 0

    def test_truthiness(self):
        assert not self.min_heap
        self.min_heap.push(5)
        assert self.min_heap

    def test_heap_repr(self):
        self.min_heap.push(1).push(2)
        assert repr(self.min_heap) == "KHeap(min, k=3, heap=[1, 2])"
        self.max_heap.push(1).push(2)
        assert repr(self.max_heap) == "KHeap(max, k=3, heap=[2, 1])"
