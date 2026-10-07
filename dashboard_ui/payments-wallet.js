const state = { summary: null, page: 0, data: null };
        const number = value => new Intl.NumberFormat('en-GB').format(value);
        const formatAmount = value => value !== null && value !== undefined ? Number(value).toFixed(2) : '—';
        const formatDate = value => value ? new Date(value).toLocaleDateString() : '—';

        async function loadSummary() {
            try {
                console.log('Fetching /api/payments-wallet-summary...');
                const response = await fetch('/api/payments-wallet-summary');
                console.log('Response status:', response.status);
                if (!response.ok) throw new Error('Failed to load summary: ' + response.status);
                const data = await response.json();
                console.log('Data received:', data);
                state.summary = data;
                renderSummary();
            } catch (error) {
                console.error('Error loading summary:', error);
                showError('Error: ' + error.message);
            }
        }

        function renderSummary() {
            try {
                const s = state.summary;
                if (!s) {
                    console.error('No summary data available');
                    return;
                }
                console.log('Updating elements with:', s);

                const atlasEl = document.getElementById('atlas-total');
                const walletEl = document.getElementById('wallet-total');
                const matchedEl = document.getElementById('matched');
                const missingEl = document.getElementById('missing-in-wallet');

                if (atlasEl) atlasEl.textContent = number(s.atlas_total);
                if (walletEl) walletEl.textContent = number(s.wallet_total);
                if (matchedEl) matchedEl.textContent = number(s.matched);
                if (missingEl) missingEl.textContent = number(s.missing_in_wallet);

                console.log('Elements updated successfully');
            } catch (error) {
                console.error('Error rendering summary:', error);
                showError('Error displaying summary: ' + error.message);
            }
        }

        async function loadData(page = 0) {
            try {
                const response = await fetch(`/api/payments-wallet-missing?page=${page}`);
                if (!response.ok) throw new Error('Failed to load transactions');
                state.data = await response.json();
                state.page = page;
                renderData();
            } catch (error) {
                showError(error.message);
            }
        }

        const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));

        function renderData() {
            const data = state.data;
            const tbody = document.getElementById('rows');

            document.getElementById('case-count').textContent = number(data.total);
            if (!data.records.length) {
                document.getElementById('page-info').textContent = 'No missing transactions';
                tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No missing transactions</td></tr>';
                document.getElementById('next').disabled = true;
                document.getElementById('previous').disabled = true;
                return;
            }

            tbody.innerHTML = data.records.map(r => `
                <tr>
                    <td><span class="reference">${escapeHtml(r['Atlas Transaction ID'] || '—')}</span></td>
                    <td><span class="customer-name">${escapeHtml(r['Customer Name'] || '—')}</span></td>
                    <td class="amount-column"><span class="amount">${formatAmount(r['Amount'])}</span> <span class="amount-currency">${escapeHtml(r['Currency'] || '')}</span></td>
                    <td>${escapeHtml(r['Payment Status'] || '—')}</td>
                    <td>${escapeHtml(r['Payment Operator'] || '—')}</td>
                    <td>${formatDate(r['Transaction Date'])}</td>
                </tr>
            `).join('');

            const total = data.total;
            const pages = Math.ceil(total / data.limit);
            document.getElementById('case-count').textContent = number(total);
            document.getElementById('page-info').textContent = `Page ${state.page + 1} of ${pages}`;
            document.getElementById('previous').disabled = state.page === 0;
            document.getElementById('next').disabled = !data.has_more;
        }

        function showError(message) {
            document.getElementById('error').hidden = false;
            document.getElementById('error').textContent = message;
        }

        document.getElementById('previous').addEventListener('click', () => {
            if (state.page > 0) loadData(state.page - 1);
        });

        document.getElementById('next').addEventListener('click', () => {
            if (state.data && state.data.has_more) loadData(state.page + 1);
        });

        loadSummary();
        loadData();

const paymentsToggle = document.getElementById('payments-toggle');
        const paymentsMenu = document.getElementById('payments-menu');

        // Load saved state from localStorage
        const isOpen = localStorage.getItem('payments-menu-open') !== 'false';
        if (!isOpen) {
            paymentsMenu.classList.add('hidden');
        }

        paymentsToggle.addEventListener('click', () => {
            const isCurrentlyOpen = !paymentsMenu.classList.contains('hidden');
            if (isCurrentlyOpen) {
                paymentsMenu.classList.add('hidden');
                localStorage.setItem('payments-menu-open', 'false');
            } else {
                paymentsMenu.classList.remove('hidden');
                localStorage.setItem('payments-menu-open', 'true');
            }
        });

        document.addEventListener('data-imported', () => {
            document.getElementById('error').hidden = true;
            loadSummary();
            loadData(0);
        });
