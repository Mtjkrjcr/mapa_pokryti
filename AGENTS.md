# AGENTS.md

## Pravidla pro úpravy kódu

- Každá nová nebo výrazně upravená část kódu musí obsahovat stručné vysvětlující komentáře tak, aby byla srozumitelná pro člověka, který projekt čte poprvé.
- Preferuj komentáře po blocích/funkcích (co a proč), ne zahlcující komentáře na každý řádek.
- U netriviální logiky (parsing, mapové vrstvy, filtry, párování dat, externí CLI/GDAL kroky) vždy doplň komentář k účelu a klíčovému rozhodnutí.
- Při refaktoringu zachovej nebo aktualizuj existující komentáře, aby odpovídaly aktuálnímu chování.
