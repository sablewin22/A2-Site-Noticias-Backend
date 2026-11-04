# Colocar o Entrelinhas no ar (uma vez só)

São 5 partes. Cada uma tem quem faz, porque algumas precisam do banco (que só
está no computador da Gabriela) e outras precisam da conta dona do repositório.

> **O repositório precisa continuar PRIVADO.** O banco fica num "release" do
> próprio repositório e tem os textos completos das matérias. Se o repositório
> virar público, o banco vira público junto.

---

## Parte 1: subir o código novo para o `main`
**Quem faz:** quem tem o repositório no GitHub Desktop.

O upload pelo site do GitHub não serve aqui: ele não aceita a pasta `.github`
nem mais de 100 arquivos de uma vez (só as capas são mais de 700).

1. Instale o [GitHub Desktop](https://desktop.github.com) e entre com a sua conta.
2. **File > Clone repository**, escolha `A2-Site-Noticias-Backend` e clone.
   Anote a pasta (normalmente `Documentos\GitHub\A2-Site-Noticias-Backend`).
3. No alto do GitHub Desktop, confira que **Current branch** é `main`.
4. No PowerShell, copie os arquivos novos por cima (troque o caminho se o zip
   ou a pasta do clone estiverem em outro lugar):
   ```
   Expand-Archive "$HOME\Downloads\entrelinhas_vercel.zip" "$HOME\Downloads\entrelinhas_vercel" -Force
   $repo = "$HOME\Documents\GitHub\A2-Site-Noticias-Backend"
   Copy-Item "$HOME\Downloads\entrelinhas_vercel\entrelinhas\*" $repo -Recurse -Force
   Remove-Item "$repo\CLASSIFICACAO.md", "$repo\preparar_pacote.py" -ErrorAction SilentlyContinue
   ```
5. Volte ao GitHub Desktop. À esquerda aparecem os arquivos mudados (inclusive
   `site/capas/...`). **Não pode aparecer nenhum `.db`, `.zip` ou `.gemini_key`.**
6. Em **Summary** escreva `Versão automática com Vercel`, clique em
   **Commit to main** e depois em **Push origin**.

O ramo `jamilly` consolida o backend com Gemini, a sincronização diária com o Supabase e o frontend do Vercel. Tudo é integrado na branch `main`.
O Supabase armazena a base relacional oficial na nuvem (`acontecimentos` e `noticias`) e o Vercel publica o site.

---

## Parte 2: guardar o banco no release "dados"
**Quem faz:** a Gabriela (o banco está no computador dela).

1. Na pasta de trabalho do projeto, no PowerShell:
   ```
   Compress-Archive -Path noticias.db, embeddings.npz -DestinationPath banco.zip -Force
   ```
2. No site do repositório, clique em **Releases** (coluna da direita) >
   **Draft a new release** (ou **Create a new release**).
3. Em **Choose a tag**, digite `dados` e clique em **Create new tag: dados on publish**.
4. Em **Release title**, escreva `dados`.
5. Arraste o `banco.zip` para a área **Attach binaries**. Espere terminar de subir.
6. Clique em **Publish release**.

O nome do arquivo precisa ser exatamente `banco.zip` e a tag exatamente `dados`.
Depois disso, **o banco oficial passa a ser o do release**: a atualização diária
baixa, atualiza e sobe de volta todo dia.

---

## Parte 3: chaves do Gemini e do Supabase
**Quem faz:** a conta dona do repositório.

1. As chaves `GEMINI_API_KEY`, `SUPABASE_URL` e `SUPABASE_KEY` já estão cadastradas nos Secrets do repositório (**Settings > Secrets and variables > Actions**).
2. O workflow diário utiliza essas variáveis automaticamente para classificar as matérias com o Gemini e sincronizar os dados na nuvem com o Supabase.

---

## Parte 4: Vercel
**Quem faz:** a conta dona do repositório.

1. Entre em [vercel.com](https://vercel.com) com o GitHub.
2. **Add New > Project**, escolha `A2-Site-Noticias-Backend` > **Import**.
3. Em **Root Directory**, clique em **Edit** e escolha a pasta `site`.
4. **Framework Preset**: `Other`. Não precisa mudar mais nada. **Deploy**.
5. Para o site atualizar sozinho com certeza: no projeto do Vercel, vá em
   **Settings > Git > Deploy Hooks**, crie um com o nome `diaria` e o ramo
   `main`, e copie o endereço gerado.
6. No GitHub, crie outro secret (como na Parte 3) com o nome
   `VERCEL_DEPLOY_HOOK` e o endereço copiado.

---

## Parte 5: primeira rodada
**Quem faz:** qualquer pessoa com acesso ao repositório.

1. No repositório, aba **Actions**. Se aparecer um aviso para habilitar os
   workflows, habilite.
2. Clique em **Atualização diária** > **Run workflow** > ramo `main` > **Run workflow**.
3. Clique na rodada que apareceu para acompanhar cada etapa. A primeira demora
   mais (cerca de 1 hora), porque classifica as ~3.000 notícias que ainda não
   têm classificação. As seguintes só processam o que é novo.
4. Quando ficar verde, abra o site no Vercel: as colunas da classificação
   aparecem em cada acontecimento.

Depois disso, ela roda sozinha todo dia às 6h (Brasília).

### Se algo der errado
- **Erro em "Baixar o banco"**: o release não se chama `dados` ou o arquivo não
  se chama `banco.zip` (Parte 2).
- **Etapa 1 com quase nenhuma notícia nova ou muitos erros**: o Google News pode
  estar bloqueando os servidores do GitHub. Mande o registro da etapa para a Gabriela.
- **Etapa 8 com erro de chave**: confira o secret `GEMINI_API_KEY` (Parte 3). Se
  a classificação falhar, o resto continua e o site atualiza sem as novas classificações.
- **Muitos erros 429 na etapa 8**: limite de uso do Gemini. As que faltarem são
  refeitas no dia seguinte; para acelerar, ative o faturamento no Google AI Studio.
- **Site não atualizou**: confira a Parte 4, passos 5 e 6.

Cada rodada usa minutos do GitHub Actions. A conta gratuita tem uma cota mensal
para repositórios privados; o uso aparece em **Settings > Billing** da conta dona.
