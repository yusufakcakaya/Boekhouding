function deleteTx(id) {
    if (!confirm("Weet je zeker dat je deze transactie wilt verwijderen?")) return;

    fetch("/delete_transaction", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "tx_id=" + id
    })
    .then(r => {
        if (r.ok) location.reload();
        else alert("Verwijderen mislukt");
    });
}
