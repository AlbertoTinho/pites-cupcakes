# Cupcakes Gourmet — PITE II

Primeira versão local com Python, Flask, Jinja e SQLite (`sqlite3`). Cadastro de clientes, login, catálogo, carrinho, revisão de entrega, pagamento simulado, acompanhamento dos próprios pedidos e administração de produtos e status. Interface adaptável a celular e computador.

## Rodar no Windows (PowerShell)

Execute **um comando por vez**. Não é necessário ativar o ambiente virtual nem alterar a política de execução do PowerShell.

1. Abra o PowerShell na pasta do projeto:

```powershell
Set-Location 'C:\Users\alber\OneDrive\Alberto\Engenharia de Software\PITESoftware'
```

2. Confira se o Python está instalado:

```powershell
py --version
```

Se `py` não for reconhecido, instale Python 3.12 ou superior pelo instalador oficial de Python, incluindo o launcher e a opção de adicionar Python ao PATH. Feche e abra o PowerShell novamente. Se você já usa o comando `python`, pode substituir apenas `py` por `python` no próximo comando.

3. Crie o ambiente isolado:

```powershell
py -m venv .venv
```

4. Instale as dependências:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

5. Inicie o site usando o banco **existente**, que será preservado:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run
```

6. Deixe esse terminal aberto. Em outro PowerShell, abra o navegador:

```powershell
Start-Process 'http://127.0.0.1:5000'
```

Use Ctrl+C no terminal do servidor para parar. O servidor é para demonstração local. Recuperação de senha, busca de CEP e pagamentos reais ficam para outra etapa.

## Criar o administrador

Na pasta do projeto, execute em outro terminal (ou pare o servidor):

```powershell
.\.venv\Scripts\python.exe -m flask --app app create-admin
```

Informe nome, um e-mail novo e uma senha de pelo menos oito caracteres, duas vezes. A senha não aparece enquanto você digita. O comando não promove contas existentes. Entre pelo login normal e clique em **Administração**. Cadastre produtos, ajuste estoque e preço e desmarque **Produto ativo** para desativar sem apagar o histórico. Em **Gerenciar pedidos**, altere o status. Cancelar apenas muda o status; se houver reposição física, ajuste o estoque manualmente.

Todo cadastro feito pelo site é de cliente; o perfil enviado pelo navegador não é usado. As permissões administrativas e a propriedade dos pedidos são conferidas no servidor.

## Banco novo opcional com cupcakes fictícios

O arquivo original é `BD\pitesoftware_cupcakes.db`. Não é preciso inicializá-lo novamente. O esquema em `BD\schema.sql` já atende aos requisitos; nenhuma migração foi necessária e nenhuma alteração de estrutura é feita ao iniciar o site.

Para experimentar sem alterar os registros originais, crie outro banco:

```powershell
.\.venv\Scripts\python.exe -m flask --app app init-db --database BD\demonstracao.db --seed
```

Sem `--seed`, o banco novo fica vazio. O comando **recusa qualquer arquivo existente**. Para usar esse banco nesta sessão do PowerShell:

```powershell
$env:FLASK_DATABASE = 'BD\demonstracao.db'
```

```powershell
.\.venv\Scripts\python.exe -m flask --app app run
```

O comando `create-admin` também usa essa seleção. Para voltar ao banco original, pare o servidor e remova a variável:

```powershell
Remove-Item Env:FLASK_DATABASE
```

## Testar

Teste automatizado, sempre em arquivos temporários separados do banco original:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Para conferir manualmente no banco de demonstração: cadastre um cliente, entre, escolha um cupcake, adicione duas unidades, altere quantidades no carrinho e finalize. Digite o endereço, confira o resumo e confirme o **pagamento simulado**. Anote o número do pedido e abra **Meus pedidos**. Confira que o estoque diminuiu na quantidade comprada. Tente uma quantidade maior que o estoque e confirme a recusa. Em outra conta de cliente, tente abrir `/pedidos/NUMERO`: deve retornar 404 para o pedido alheio. `/admin` deve retornar 403 para clientes.

## Conferir no DB Browser for SQLite

1. Abra o DB Browser e escolha **Abrir banco de dados**.
2. Selecione `BD\pitesoftware_cupcakes.db` ou `BD\demonstracao.db`, conforme o banco usado no servidor.
3. Na aba **Navegar dados**, escolha `pedidos`. Veja o `id`, `usuario_id`, endereço, status e `total_centavos`.
4. Escolha `itens_pedido` e procure o mesmo `pedido_id`. Confira `produto_id`, `quantidade` e `preco_unitario_centavos`.
5. Escolha `produtos` e confira `estoque`. O preço 1200 significa R$ 12,00. Datas são gravadas em UTC.
6. Reabra/atualize a visualização depois de uma compra. Evite deixar alterações pendentes no DB Browser enquanto usa o site para não bloquear a gravação.

Na aba **Executar SQL**, esta consulta facilita a conferência:

```sql
SELECT p.id AS pedido, p.usuario_id, p.status, p.total_centavos,
       pr.nome, i.quantidade, i.preco_unitario_centavos
FROM pedidos p
JOIN itens_pedido i ON i.pedido_id = p.id
JOIN produtos pr ON pr.id = i.produto_id
ORDER BY p.id DESC;
```

## Decisões da implementação

- Consultas parametrizadas e `PRAGMA foreign_keys = ON` em toda conexão da aplicação.
- Valores inteiros em centavos; o preço da compra é registrado em cada item.
- Carrinho guarda somente IDs e quantidades. Totais são calculados no servidor. A revisão temporária guarda endereço, IDs/quantidades e total para detectar mudanças de preço.
- Confirmação usa `BEGIN IMMEDIATE`: verifica estoque/atividade, grava pedido e itens e desconta estoque na mesma transação. Qualquer falha provoca rollback. Novo envio após sucesso não cria outro pedido.
- Senhas com hash do Werkzeug, proteção CSRF nos formulários e cookies HttpOnly/SameSite. A chave é gerada localmente em `instance/secret.key` ou pode ser fornecida em `SECRET_KEY` no ambiente.
- `.gitignore` exclui bancos, chaves, ambiente virtual e caches. Preserve cópias de segurança locais do banco; não o publique no Git. O esquema e os testes permitem recriar o ambiente.
