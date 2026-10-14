(() => {
    'use strict';
    const countries = ['Uganda', 'Kenya', 'Rwanda'];
    const countryInput = document.getElementById('due-country');
    const initial = new URLSearchParams(window.location.search).get('country') || '';
    countryInput.value = countries.includes(initial) ? initial : '';

    function applyCountry(updateUrl = true) {
        const country = countries.includes(countryInput.value) ? countryInput.value : '';
        const scope = country || 'all countries';
        document.getElementById('due-scope-note').textContent = !country || country === 'Uganda'
            ? 'Uganda No due rule: Remarks = Settlement of Rent and Union Due ID (New) is null, empty or whitespace-only.'
            : 'The No due rule for ' + country + ' has not been configured yet.';
        document.getElementById('due-source-title').textContent = country
            ? 'No due-payment data loaded for ' + country
            : 'No due-payment data loaded yet';
        document.getElementById('due-source-caption').textContent = 'Country filter: ' + scope + '. Results will appear once your due-payment and due data is loaded.';
        document.getElementById('due-empty-title').textContent = country
            ? 'Awaiting ' + country + ' due-payment data'
            : 'Ready for due-payment data';
        document.getElementById('due-empty-caption').textContent = !country || country === 'Uganda' ? 'Uganda payments with Remarks = Settlement of Rent and no Union Due ID (New) will appear here once data is available.' : 'The No due rule for ' + country + ' is awaiting configuration.';
        if (updateUrl) {
            const url = new URL(window.location.href);
            if (country) url.searchParams.set('country', country);
            else url.searchParams.delete('country');
            window.history.replaceState(null, '', url.pathname + url.search + url.hash);
        }
    }
    document.getElementById('due-filters').addEventListener('submit', event => {
        event.preventDefault();
        applyCountry();
    });
    document.getElementById('due-reset').addEventListener('click', () => {
        countryInput.value = '';
        applyCountry();
    });
    applyCountry(false);
})();
