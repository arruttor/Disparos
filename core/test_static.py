"""Regressão de estáticos no processo WSGI, sem ajuda do runserver."""
import gzip
import re
from tempfile import TemporaryDirectory

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.test import Client, SimpleTestCase, override_settings


class EstaticosWSGITests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pasta = TemporaryDirectory(prefix='disparos-estaticos-')
        cls.addClassCleanup(cls.pasta.cleanup)
        cls.config = override_settings(
            STATIC_ROOT=cls.pasta.name,
            STATIC_URL='/static/',
            STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
            },
            WHITENOISE_USE_FINDERS=False,
            WHITENOISE_AUTOREFRESH=False,
        )
        cls.config.enable()
        cls.addClassCleanup(cls.config.disable)
        call_command('collectstatic', interactive=False, verbosity=0)

    def conteudo(self, resposta):
        try:
            return b''.join(resposta.streaming_content) if resposta.streaming else resposta.content
        finally:
            resposta.close()

    def test_css_js_e_admin_disponiveis_com_debug_ligado_e_desligado(self):
        for debug in (True, False):
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                cliente = Client()
                for arquivo, tipo in (
                    ('css/painel.css', 'text/css'),
                    ('css/admin.css', 'text/css'),
                    ('js/painel.js', 'javascript'),
                    ('img/favicon.svg', 'image/svg+xml'),
                    ('admin/css/base.css', 'text/css'),
                ):
                    with self.subTest(arquivo=arquivo):
                        url = staticfiles_storage.url(arquivo)
                        if not debug:
                            self.assertRegex(url, r'\.[0-9a-f]{12}\.\w+$')
                        resposta = cliente.get(url)
                        self.assertEqual(resposta.status_code, 200)
                        self.assertIn(tipo, resposta['Content-Type'])
                        self.assertTrue(self.conteudo(resposta))

    @override_settings(DEBUG=False)
    def test_css_comprimido_e_cache_para_nome_com_hash(self):
        url = staticfiles_storage.url('css/painel.css')
        resposta = Client().get(url, HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Encoding'], 'gzip')
        self.assertIn('immutable', resposta['Cache-Control'])
        self.assertIn(b'--accent', gzip.decompress(self.conteudo(resposta)))

    @override_settings(DEBUG=False)
    def test_pagina_de_login_referencia_css_servido(self):
        cliente = Client()
        pagina = cliente.get('/entrar/')
        self.assertEqual(pagina.status_code, 200)
        html = pagina.content.decode()
        url = re.search(r'href="([^"]*css/painel\.[^"]+\.css)"', html).group(1)
        resposta = cliente.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(b'--accent', self.conteudo(resposta))
