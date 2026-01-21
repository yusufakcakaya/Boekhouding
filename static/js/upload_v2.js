const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("csvInput");
const filePreview = document.getElementById("filePreview");

// CLICK OPENS FILE SELECT
dropzone.addEventListener("click", () => fileInput.click());

// FILE START
fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) {
        filePreview.textContent = "Geselecteerd: " + fileInput.files[0].name;
    }
});

// DRAG OVER
dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
    dropzone.style.background = "#fff0f7";
});

// DRAG LEAVE
dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
    dropzone.style.background = "#fff9fc";
});

// DROP
dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");

    const file = e.dataTransfer.files[0];

    if (file && file.name.endsWith(".csv")) {
        fileInput.files = e.dataTransfer.files;
        filePreview.textContent = "Geselecteerd: " + file.name;
        dropzone.style.background = "#fff9fc";
    } else {
        filePreview.textContent = "❌ Ongeldig bestand – alleen .csv toegestaan";
    }
});



