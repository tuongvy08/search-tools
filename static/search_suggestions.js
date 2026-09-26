/* Explicit search is separate from the lightweight, cancellable suggestion GET. */
function initProductSuggestions(input, search) {
    if (!input) return {dismiss() {}};
    const list = document.getElementById('searchSuggestions');
    const status = document.getElementById('searchSuggestStatus');
    let timer, controller, generation = 0, composing = false, selected = -1, items = [];
    function dismiss() {
        generation += 1;
        clearTimeout(timer);
        controller?.abort();
        controller = null;
        items = [];
        selected = -1;
        list.replaceChildren();
        list.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        status.textContent = '';
    }
    function choose(index) {
        const item = items[index];
        if (!item) return;
        input.value = item.value;
        dismiss();
        search();
    }
    function highlight(index) {
        selected = index;
        [...list.children].forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
        if (index >= 0) {
            input.setAttribute('aria-activedescendant', list.children[index].id);
            list.children[index].scrollIntoView({block: 'nearest'});
        }
    }
    async function load(query, version) {
        controller = new AbortController();
        const signal = controller.signal;
        try {
            const response = await fetch(`/search/suggestions?query=${encodeURIComponent(query)}`,
                {signal, credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
            if (!response.ok || response.redirected) throw new Error('unavailable');
            const data = await response.json();
            if (version !== generation || signal.aborted || composing || document.activeElement !== input || input.value.trim() !== query) return;
            items = (data.suggestions || []).slice(0, 10);
            list.replaceChildren();
            items.forEach((item, i) => {
                const option = document.createElement('li');
                option.id = `search-suggest-${version}-${i}`;
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', 'false');
                option.textContent = item.label;
                // Keep focus through the compatibility mouse event on a completed
                // tap, but do not select on touch-down: native scrolling must win.
                option.addEventListener('mousedown', event => event.preventDefault());
                option.addEventListener('click', event => {
                    if (event.button === 0) choose(i);
                });
                list.appendChild(option);
            });
            list.hidden = !items.length;
            input.setAttribute('aria-expanded', String(items.length > 0));
            status.textContent = data.degraded ? 'Gợi ý chưa đầy đủ. Bấm Search để tìm kiếm.' :
                items.length ? `${items.length} gợi ý. Dùng phím mũi tên để chọn.` : 'Không có gợi ý. Bạn vẫn có thể bấm Search.';
        } catch (error) {
            if (version === generation && !signal.aborted && document.activeElement === input)
                status.textContent = 'Gợi ý tạm thời không khả dụng. Bạn vẫn có thể bấm Search.';
        }
    }
    function schedule() {
        dismiss();
        const query = input.value.trim();
        if (composing || query.length < 3 || query.length > 500 || document.activeElement !== input) return;
        const version = generation;
        timer = setTimeout(() => load(query, version), 300);
    }
    input.addEventListener('input', schedule);
    input.addEventListener('focus', schedule);
    input.addEventListener('blur', dismiss);
    input.addEventListener('compositionstart', () => { composing = true; dismiss(); });
    input.addEventListener('compositionend', () => { composing = false; schedule(); });
    input.addEventListener('keydown', event => {
        if (composing || event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Escape') { event.preventDefault(); dismiss(); }
        else if (event.key === 'ArrowDown' && items.length) {
            event.preventDefault(); highlight((selected + 1) % items.length);
        } else if (event.key === 'ArrowUp' && items.length) {
            event.preventDefault(); highlight(selected < 0 ? items.length - 1 : (selected - 1 + items.length) % items.length);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (selected >= 0) choose(selected);
            else { dismiss(); search(); }
        }
    });
    document.addEventListener('pointerdown', event => {
        if (event.target !== input && !list.contains(event.target)) dismiss();
    });
    return {dismiss};
}
if (typeof module !== 'undefined') module.exports = {initProductSuggestions};
